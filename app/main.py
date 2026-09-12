from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.api.router import api_router
from app.api.v1 import lab
from app.application.standalone import BaraqAIApplication
from app.core.body_limit import MaxRequestBodyMiddleware
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from app.lab.jobs import LocalJobManager
from app.lab.providers import build_lab_provider
from app.lab.storage import LabStorage
from app.schemas.common import APIEnvelope, APIError

settings = get_settings()
configure_logging()
logger = get_logger(__name__)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.app_version,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "service_starting",
        version=settings.app_version,
        environment=settings.app_env,
        runtime_mode=settings.baraq_runtime_mode,
    )
    lab_enabled_this_process = settings.baraq_runtime_mode == "lab" or (
        settings.enable_ai_lab and settings.app_env != "production"
    )
    if lab_enabled_this_process:
        storage = LabStorage(settings)
        storage.initialize()
        provider = build_lab_provider(settings)
        application = BaraqAIApplication(settings=settings, storage=storage, provider=provider)
        app.state.lab_storage = storage
        app.state.lab_application = application
        app.state.lab_jobs = LocalJobManager(storage=storage, application=application)
    yield
    if settings.baraq_runtime_mode == "service":
        await get_redis().aclose()
    logger.info("service_stopped")


app = FastAPI(
    title="Baraq AI Service",
    version=settings.app_version,
    description="Standalone AI platform for Fahes, Khota, Rasheed, Kholasa and Sada.",
    docs_url=f"{settings.public_api_prefix}/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    openapi_url=(
        f"{settings.public_api_prefix}/openapi.json" if settings.app_env != "production" else None
    ),
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-Baraq-Service",
            "X-Baraq-Key-Id",
            "X-Baraq-Timestamp",
            "X-Baraq-Nonce",
            "X-Content-SHA256",
            "X-Baraq-Signature",
        ],
    )
# Added last so it becomes the outermost user-level layer (Starlette wraps
# most-recently-added middleware around everything else) -- a request this
# large is rejected before TrustedHost/CORS/routing/HMAC verification ever
# touch it, not just before the route handler.
app.add_middleware(MaxRequestBodyMiddleware, max_bytes=settings.max_request_body_bytes)


@app.middleware("http")
async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> ORJSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return ORJSONResponse(
        status_code=exc.status_code,
        content=APIEnvelope(
            success=False,
            error=APIError(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=request_id,
            ),
        ).model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> ORJSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    errors = [{"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
    code = (
        "invalid_task_input"
        if any("input" in error["loc"] for error in errors)
        else "invalid_contract"
    )
    return ORJSONResponse(
        status_code=422,
        content=APIEnvelope(
            success=False,
            error=APIError(
                code=code,
                message="Request does not match the required contract",
                details={"errors": errors},
                request_id=request_id,
            ),
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> ORJSONResponse:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    logger.exception("unhandled_error", request_id=request_id, error=str(exc))
    return ORJSONResponse(
        status_code=500,
        content=APIEnvelope(
            success=False,
            error=APIError(
                code="internal_server_error",
                message="حدث خطأ داخلي غير متوقع",
                request_id=request_id,
            ),
        ).model_dump(mode="json"),
    )


if settings.baraq_runtime_mode == "lab":
    app.include_router(lab.router)
else:
    app.include_router(api_router, prefix=settings.public_api_prefix)
    # Hard gate, not a hidden button (spec section 30): the Lab router is
    # only ever added to the ASGI route table outside production. In
    # production it is never mounted, so every /lab/* path is a genuine 404.
    if settings.enable_ai_lab and settings.app_env != "production":
        app.include_router(lab.router)
