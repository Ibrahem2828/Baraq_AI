"""Transaction-scope regression for JobService's idempotency lookup.

POST /api/ai/v1/jobs calls `existing_idempotency_result()` and then, when no
earlier job exists, `create_job()`, which opens `async with session.begin()`.
The lookup used to run a bare SELECT, which autobegins a transaction that it
never closed, so every new job failed in production with
"A transaction is already begun on this Session" -- a 500 back to Django.

A real AsyncSession is used on purpose: a mocked session cannot reproduce
SQLAlchemy's autobegin semantics, which is exactly how this shipped.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.ai_job import AIJob
from app.schemas.jobs import JobCreateRequest, ModelPolicy, TraceContext
from app.services.job_service import JobService


def _unvalidated(_task_type: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return payload


async def test_idempotency_lookup_leaves_the_session_ready_for_create_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        # The model uses PostgreSQL column types; an empty SELECT only needs
        # the table and its column names to exist.
        columns = ", ".join(f'"{column.name}"' for column in AIJob.__table__.columns)
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE TABLE "{AIJob.__tablename__}" ({columns})'))

        # Task-input schemas are unrelated to transaction state.
        monkeypatch.setattr(JobService, "validate_payload", staticmethod(_unvalidated))
        # The shape DjangoJobCreateRequestV2.to_internal() hands the endpoint.
        request = JobCreateRequest(
            client_job_id=str(uuid.uuid4()),
            task_type="fahes_generate_quiz",
            project_id="project-tx-001",
            input={"source_ids": ["1"]},
            model_policy=ModelPolicy(tier="balanced", allow_fallback=True),
            trace=TraceContext(request_id=uuid.uuid4()),
        )

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = JobService(session)

            existing = await service.existing_idempotency_result(
                user_id="user-tx-001", request=request
            )
            assert existing is None
            assert not session.in_transaction()

            # What create_job() does next; this raised InvalidRequestError before the fix.
            async with session.begin():
                pass
    finally:
        await engine.dispose()
