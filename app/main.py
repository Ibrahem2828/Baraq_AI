from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
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
async def lifespan(_: FastAPI):
    logger.info("service_starting", version=settings.app_version, environment=settings.app_env)
    yield
    await get_redis().aclose()
    logger.info("service_stopped")


app = FastAPI(
    title="Baraq AI Service",
    version=settings.app_version,
    description="Standalone AI platform for Fahes, Khota, Rasheed, Kholasa and Sada.",
    docs_url=f"{settings.public_api_prefix}/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    openapi_url=(
        f"{settings.public_api_prefix}/openapi.json"
        if settings.app_env != "production"
        else None
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
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001
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


app.include_router(api_router, prefix=settings.public_api_prefix)
