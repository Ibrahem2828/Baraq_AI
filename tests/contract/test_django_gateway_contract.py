from __future__ import annotations

from pathlib import Path

from app.core.endpoints import BACKEND_ENDPOINTS

ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_django_gateway_routes_are_the_only_operational_job_routes() -> None:
    jobs = _source("app/api/v1/jobs.py")
    feedback = _source("app/api/v1/feedback.py")
    router = _source("app/api/router.py")
    main = _source("app/main.py")

    assert 'router = APIRouter(prefix="/jobs", tags=["Django Gateway Jobs"])' in jobs
    assert '@router.post("", response_model=APIEnvelope[JobAccepted]' in jobs
    assert '@router.get("/{job_id}", response_model=APIEnvelope[JobView])' in jobs
    assert '@router.post("/{job_id}/cancel", response_model=APIEnvelope[JobView])' in jobs
    assert 'router = APIRouter(prefix="/feedback", tags=["Django Gateway Feedback"])' in feedback
    assert "include_router(characters.router)" not in router
    assert "include_router(admin.router)" not in router
    assert '@app.get("/metrics"' not in main


def test_gateway_job_routes_require_django_service_signature() -> None:
    jobs = _source("app/api/v1/jobs.py")
    feedback = _source("app/api/v1/feedback.py")
    dependencies = _source("app/api/dependencies.py")

    assert "DjangoService" in jobs
    assert "DjangoService" in feedback
    assert "async def require_django_service(request: Request)" in dependencies
    assert "verify_and_consume_service_signature(" in dependencies
    assert "X-Baraq-Key-Id" not in dependencies  # header handling is centralized in security.py
    assert "HTTPBearer" not in dependencies
    assert "decode_access_token" not in dependencies


def test_django_internal_endpoint_contract_has_no_credit_or_materialize_calls() -> None:
    assert BACKEND_ENDPOINTS.source_manifest("source-1") == (
        "/api/internal/v1/ai/sources/source-1/manifest/"
    )
    assert BACKEND_ENDPOINTS.source_download("source-1") == (
        "/api/internal/v1/ai/sources/source-1/download/"
    )
    assert BACKEND_ENDPOINTS.collection_manifest("collection-1") == (
        "/api/internal/v1/ai/collections/collection-1/manifest/"
    )
    assert BACKEND_ENDPOINTS.learner_context("user-1") == (
        "/api/internal/v1/ai/users/user-1/context/"
    )
    assert BACKEND_ENDPOINTS.job_webhook == "/api/internal/v1/ai/webhooks/jobs/"
    assert not hasattr(BACKEND_ENDPOINTS, "credits_reserve")
    assert not hasattr(BACKEND_ENDPOINTS, "materialize")
