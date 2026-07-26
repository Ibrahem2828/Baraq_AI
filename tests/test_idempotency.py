from app.core.idempotency import build_idempotency_scope, stable_hash


def test_stable_hash_is_order_independent() -> None:
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_idempotency_is_scoped_by_user_and_task() -> None:
    first = build_idempotency_scope(user_id="1", task_type="fahes", key="request-123")
    second = build_idempotency_scope(user_id="2", task_type="fahes", key="request-123")
    assert first != second
