from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.dependencies as dependencies
from app.api.dependencies import require_django_service
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.security import AuthenticatedDjangoService, make_service_signature
from app.db.session import get_db_session
from app.main import app
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.jobs import JobCreateRequest
from app.services.job_service import JobService


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    async def authenticated_django() -> AuthenticatedDjangoService:
        return AuthenticatedDjangoService(service_id="baraq-django", key_id="django-current")

    async def fake_session() -> object:
        yield object()

    async def fake_freeze_source_versions(**_: object) -> dict[str, str]:
        return {"source-1": "a" * 64}

    async def fake_existing_idempotency_result(self: JobService, **_: object) -> None:
        return None

    async def fake_create_job(
        self: JobService,
        *,
        user_id: str,
        request: JobCreateRequest,
        source_versions: dict[str, str],
    ) -> tuple[SimpleNamespace, bool]:
        assert user_id == "user-1"
        assert source_versions == {"source-1": "a" * 64}
        return (
            SimpleNamespace(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                backend_request_id=request.client_job_id,
                request_id=str(request.trace.request_id),
                status=JobStatus.QUEUED,
                task_type=TaskType.FAHES_GENERATE_QUIZ,
                character=Character.FAHES,
            ),
            False,
        )

    monkeypatch.setattr(JobService, "create_job", fake_create_job)
    monkeypatch.setattr(JobService, "freeze_source_versions", fake_freeze_source_versions)
    monkeypatch.setattr(JobService, "existing_idempotency_result", fake_existing_idempotency_result)
    app.dependency_overrides[require_django_service] = authenticated_django
    app.dependency_overrides[get_db_session] = fake_session
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client
    app.dependency_overrides.clear()


def valid_v2_payload() -> dict[str, object]:
    return {
        "contract_version": "2.0",
        "client_job_id": "00000000-0000-0000-0000-000000000010",
        "user_id": "user-1",
        "task_type": "fahes_generate_quiz",
        "input": {"source_ids": ["source-1"]},
        "model_policy": {"tier": "balanced", "allow_fallback": True},
        "trace": {"request_id": "00000000-0000-0000-0000-000000000011"},
    }


def test_v2_job_endpoint_accepts_the_documented_contract(client: TestClient) -> None:
    response = client.post("/api/ai/v1/jobs", json=valid_v2_payload())
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["job_id"] == "00000000-0000-0000-0000-000000000010"
    assert payload["data"]["status"] == "queued"


def test_v2_job_endpoint_rejects_extra_top_level_fields(client: TestClient) -> None:
    payload = valid_v2_payload()
    payload["source_id"] = "must-be-nested"
    response = client.post("/api/ai/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_contract"


def test_v2_job_endpoint_rejects_task_specific_invalid_input_before_worker(
    client: TestClient,
) -> None:
    payload = valid_v2_payload()
    payload["input"] = {}
    response = client.post("/api/ai/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task_input"


def test_http_route_rejects_a_tampered_signed_body_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRedis:
        async def set(self, *args: object, **kwargs: object) -> bool:
            return True

    get_settings.cache_clear()
    monkeypatch.setattr(dependencies, "get_redis", lambda: FakeRedis())
    body = b'{"contract_version":"2.0"}'
    # Sign with whatever key id is actually configured in this environment's
    # keyring (real .env or a test override) rather than a hardcoded id --
    # the two must never drift, or this becomes an "unknown_key_id" failure
    # instead of exercising the tampered-body check it's named for.
    headers = make_service_signature(
        method="POST",
        target="/api/ai/v1/jobs",
        body=body,
        service="baraq-django",
        key_id=get_settings().baraq_hmac_current_key_id,
        nonce="tampered-route-test-0001",
    )
    headers["Content-Type"] = "application/json"
    with TestClient(app, base_url="http://localhost") as test_client:
        response = test_client.post(
            "/api/ai/v1/jobs", content=b'{"contract_version":"2.1"}', headers=headers
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_content_hash"


def test_get_job_requires_user_id_query_param(client: TestClient) -> None:
    # backend_request_id is unique only per (user_id, backend_request_id) --
    # omitting user_id would let a lookup match another tenant's job.
    response = client.get("/api/ai/v1/jobs/00000000-0000-0000-0000-000000000010")
    assert response.status_code == 422


def _fake_job(*, client_job_id: str, status: JobStatus) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        backend_request_id=client_job_id,
        request_id=str(uuid.uuid4()),
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        character=Character.FAHES,
        status=status,
        progress_percent=0,
        progress_message="in progress",
        created_at=now,
        updated_at=now,
        started_at=None,
        completed_at=None,
        error_code=None,
        error_message=None,
        output=None,
    )


def test_get_job_scopes_the_lookup_by_user_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    async def fake_get_job_by_client_id(
        self: JobService, *, client_job_id: str, user_id: str
    ) -> SimpleNamespace:
        captured["client_job_id"] = client_job_id
        captured["user_id"] = user_id
        return _fake_job(client_job_id=client_job_id, status=JobStatus.QUEUED)

    monkeypatch.setattr(JobService, "get_job_by_client_id", fake_get_job_by_client_id)
    response = client.get(
        "/api/ai/v1/jobs/00000000-0000-0000-0000-000000000010?user_id=user-1"
    )
    assert response.status_code == 200, response.text
    assert captured == {
        "client_job_id": "00000000-0000-0000-0000-000000000010",
        "user_id": "user-1",
    }


def test_get_job_returns_404_when_job_belongs_to_a_different_tenant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get_job_by_client_id(
        self: JobService, *, client_job_id: str, user_id: str
    ) -> SimpleNamespace:
        raise NotFoundError("AI job not found")

    monkeypatch.setattr(JobService, "get_job_by_client_id", fake_get_job_by_client_id)
    response = client.get(
        "/api/ai/v1/jobs/00000000-0000-0000-0000-000000000010?user_id=someone-elses-id"
    )
    assert response.status_code == 404


def test_get_job_rejects_a_user_id_query_param_tampered_after_signing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HMAC signature must cover query parameters, not just the body --
    otherwise `user_id` (added to close the cross-tenant IDOR) would be a bare,
    unauthenticated claim any intermediary could rewrite. This exercises the
    real verify_and_consume_service_signature path end-to-end over HTTP, unlike
    the tenant-scoping tests above which override require_django_service."""

    class FakeRedis:
        async def set(self, *args: object, **kwargs: object) -> bool:
            return True

    async def fake_session() -> object:
        yield object()

    async def fake_get_job_by_client_id(
        self: JobService, *, client_job_id: str, user_id: str
    ) -> SimpleNamespace:
        return _fake_job(client_job_id=client_job_id, status=JobStatus.QUEUED)

    get_settings.cache_clear()
    monkeypatch.setattr(dependencies, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(JobService, "get_job_by_client_id", fake_get_job_by_client_id)
    app.dependency_overrides[get_db_session] = fake_session

    job_id = "00000000-0000-0000-0000-000000000010"
    signed_target = f"/api/ai/v1/jobs/{job_id}?user_id=user-a"
    headers = make_service_signature(
        method="GET",
        target=signed_target,
        body=b"",
        service="baraq-django",
        key_id=get_settings().baraq_hmac_current_key_id,
        nonce="user-id-tamper-test-0001",
    )

    try:
        with TestClient(app, base_url="http://localhost") as test_client:
            accepted = test_client.get(signed_target, headers=headers)
            # Same headers/signature, but user_id swapped on the wire without
            # re-signing -- must be rejected, not silently scoped to "user-b".
            tampered_target = f"/api/ai/v1/jobs/{job_id}?user_id=user-b"
            rejected = test_client.get(tampered_target, headers=headers)
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 401, rejected.text
    assert rejected.json()["error"]["code"] == "invalid_signature"


def test_cancel_job_scopes_the_lookup_by_user_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    async def fake_cancel_job_by_client_id(
        self: JobService, *, client_job_id: str, user_id: str
    ) -> SimpleNamespace:
        captured["client_job_id"] = client_job_id
        captured["user_id"] = user_id
        return _fake_job(client_job_id=client_job_id, status=JobStatus.CANCELED)

    monkeypatch.setattr(JobService, "cancel_job_by_client_id", fake_cancel_job_by_client_id)
    response = client.post(
        "/api/ai/v1/jobs/00000000-0000-0000-0000-000000000010/cancel?user_id=user-1"
    )
    assert response.status_code == 200, response.text
    assert captured == {
        "client_job_id": "00000000-0000-0000-0000-000000000010",
        "user_id": "user-1",
    }
