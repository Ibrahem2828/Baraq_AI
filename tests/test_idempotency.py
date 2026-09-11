import pytest

from app.core.errors import ConflictError
from app.core.idempotency import build_idempotency_scope, stable_hash
from app.models.ai_job import AIJob
from app.services.job_service import JobService


def test_stable_hash_is_order_independent() -> None:
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_idempotency_is_scoped_by_user_and_client_job_id() -> None:
    first = build_idempotency_scope(user_id="1", client_job_id="request-123")
    second = build_idempotency_scope(user_id="2", client_job_id="request-123")
    assert first != second


def test_same_client_job_id_with_different_payload_is_a_conflict() -> None:
    existing = AIJob(input_hash="first")
    with pytest.raises(ConflictError) as error:
        JobService._existing_idempotency_result(existing, "different")
    assert error.value.code == "idempotency_conflict"
