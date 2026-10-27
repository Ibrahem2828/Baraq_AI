"""One account cannot fan out into unlimited paid generations.

Real PostgreSQL: the cap is a count over the jobs table, so the table has to
be real. Skips unless BARAQ_AI_TEST_DATABASE_URL names a disposable database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import RateLimitError
from app.db.base import Base
from app.models.ai_job import AIJob
from app.models.enums import JobStatus, TaskType
from app.schemas.jobs import JobCreateRequest, ModelPolicy, TraceContext
from app.services.job_service import JobService

TEST_DATABASE_URL = os.environ.get("BARAQ_AI_TEST_DATABASE_URL", "").strip()

pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="Set BARAQ_AI_TEST_DATABASE_URL."),
    pytest.mark.asyncio,
]


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def request(client_job_id: str) -> JobCreateRequest:
    return JobCreateRequest(
        client_job_id=client_job_id,
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        project_id="project-1",
        input={"source_ids": ["source-1"]},
        model_policy=ModelPolicy(tier="balanced", allow_fallback=True),
        trace=TraceContext(request_id=uuid.uuid4()),
    )


VERSIONS = {"source-1": "a" * 64}


async def submit(
    maker: async_sessionmaker[AsyncSession], user_id: str, client_job_id: str
) -> AIJob:
    # One session per request, exactly as the API gives each request its own.
    async with maker() as session:
        job, _ = await JobService(session).create_job(
            user_id=user_id, request=request(client_job_id), source_versions=VERSIONS
        )
        return job


async def test_the_job_over_the_cap_is_refused_with_a_clear_code(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 2)
    await submit(maker, "user-1", "job-a")
    await submit(maker, "user-1", "job-b")

    with pytest.raises(RateLimitError) as caught:
        await submit(maker, "user-1", "job-c")

    assert caught.value.status_code == 429
    assert caught.value.details == {"code": "too_many_active_jobs", "max_active_jobs": 2}


async def test_the_cap_is_per_user(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    await submit(maker, "user-1", "job-a")

    other = await submit(maker, "user-2", "job-b")

    assert other.user_id == "user-2"


async def test_finished_jobs_free_a_slot(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    first = await submit(maker, "user-1", "job-a")
    async with maker() as other:
        stored = await other.get(AIJob, first.id)
        assert stored is not None
        stored.status = JobStatus.COMPLETED
        await other.commit()

    second = await submit(maker, "user-1", "job-b")

    assert second.status == JobStatus.QUEUED


async def test_a_replayed_request_is_answered_even_at_the_cap(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    first = await submit(maker, "user-1", "job-a")

    replay = await submit(maker, "user-1", "job-a")

    assert replay.id == first.id
