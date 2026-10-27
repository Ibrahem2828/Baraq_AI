"""The job lifecycle end to end, against a real PostgreSQL.

`process_job` is where a queued request becomes either a delivered result or a
recorded failure. The pieces around it (providers, the Django source API,
Redis) are replaced with fakes so each test pins one question about the
database state that is left behind:

* a success stores exactly one output, one result webhook, and is cached;
* a failure stores no output, no cache entry, and still queues the failure
  webhook, so Django always learns how the job ended;
* a job another worker already claimed, finished or canceled is never touched;
* flagged content is delivered flagged and is never cached for reuse.

Skips unless BARAQ_AI_TEST_DATABASE_URL names a disposable PostgreSQL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

import app.services.job_processor as processor
import app.services.result_cache as result_cache
from app.api.v1.jobs import to_job_view
from app.core.errors import ProviderError
from app.db.base import Base
from app.models.ai_job import AIJob, AIOutput, JobDispatchOutboxEvent
from app.models.enums import Character, JobStatus, ProviderAccount, TaskType
from app.models.result_cache import CachedAIResult
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.providers.base import ProviderResult, ProviderUsage
from app.schemas.common import Citation
from app.services.generation import StructuredGenerationService
from app.services.job_state_machine import TERMINAL_JOB_STATES
from app.services.knowledge_policy import KnowledgePolicy
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT, public_failure_message

TEST_DATABASE_URL = os.environ.get("BARAQ_AI_TEST_DATABASE_URL", "").strip()

pytestmark = [
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set BARAQ_AI_TEST_DATABASE_URL to a disposable PostgreSQL to run lifecycle tests.",
    ),
    pytest.mark.asyncio,
]

QUIZ = {"questions": [{"prompt": "ما عاصمة سوريا؟", "choices": ["دمشق", "حلب"], "answer": 0}]}


class FakeBackend:
    async def aclose(self) -> None:
        return None


class FakeRedis:
    async def aclose(self) -> None:
        return None


class ScriptedPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.SELECTED_SOURCES_ONLY
    version = "test-1"

    def __init__(self, behaviour: Callable[[PipelineContext], Any]) -> None:
        self.behaviour = behaviour
        self.calls = 0

    async def execute(self, context: PipelineContext) -> PipelineResult:
        self.calls += 1
        outcome = self.behaviour(context)
        if hasattr(outcome, "__await__"):
            outcome = await outcome
        return outcome  # type: ignore[no-any-return]


def good_result(**overrides: Any) -> PipelineResult:
    values: dict[str, Any] = {
        "result_json": QUIZ,
        "citations": [Citation(source_id="source-1", excerpt="دمشق عاصمة سوريا")],
        "provider_result": ProviderResult(
            data=QUIZ,
            account=ProviderAccount.PRIMARY,
            model="test-model",
            response_id="resp-1",
            usage=ProviderUsage(input_tokens=120, output_tokens=80, total_tokens=200),
            latency_ms=900,
            estimated_cost_usd=0.0123,
        ),
        "quality_score": 0.9,
        "groundedness_score": 0.95,
    }
    values.update(overrides)
    return PipelineResult(**values)


@dataclass
class Harness:
    maker: async_sessionmaker[AsyncSession]
    pipeline: ScriptedPipeline | None = None
    moderation: list[dict[str, Any]] | None = None

    async def make_job(self, **overrides: Any) -> uuid.UUID:
        values: dict[str, Any] = {
            "user_id": "user-1",
            "project_id": "project-1",
            "backend_request_id": f"client-{uuid.uuid4()}",
            "request_id": str(uuid.uuid4()),
            "task_type": TaskType.FAHES_GENERATE_QUIZ,
            "character": Character.FAHES,
            "status": JobStatus.QUEUED,
            "progress_percent": 0,
            "progress_message": "queued",
            "idempotency_hash": uuid.uuid4().hex + uuid.uuid4().hex,
            "input_hash": uuid.uuid4().hex + uuid.uuid4().hex,
            "request_payload": {"source_ids": ["source-1"]},
            "source_ids": ["source-1"],
            "source_versions": {"source-1": "a" * 64},
            "model_tier": "balanced",
            "allow_fallback": True,
        }
        values.update(overrides)
        async with self.maker() as session:
            job = AIJob(**values)
            session.add(job)
            await session.commit()
            return job.id

    async def run(self, job_id: uuid.UUID) -> None:
        await processor.process_job(str(job_id))

    async def job(self, job_id: uuid.UUID) -> AIJob:
        async with self.maker() as session:
            job = await session.scalar(
                select(AIJob).where(AIJob.id == job_id).options(selectinload(AIJob.output))
            )
            assert job is not None
            return job

    async def webhooks(self, job_id: uuid.UUID) -> int:
        return await self.count(
            JobDispatchOutboxEvent, job_id=job_id, event_type=RESULT_WEBHOOK_EVENT
        )

    async def count(self, model: Any, **filters: Any) -> int:
        async with self.maker() as session:
            query = select(func.count()).select_from(model)
            for column, value in filters.items():
                query = query.where(getattr(model, column) == value)
            return int(await session.scalar(query) or 0)


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    # NullPool: every session gets a fresh connection, so a connection that
    # one process_job call closed can never be handed to the next one.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    state = Harness(maker=maker)

    monkeypatch.setattr(processor, "AsyncSessionLocal", maker)
    # The result cache deliberately writes on its own short-lived session.
    monkeypatch.setattr(result_cache, "AsyncSessionLocal", maker)
    monkeypatch.setattr(processor, "BackendClient", FakeBackend)
    monkeypatch.setattr(Redis, "from_url", lambda *_, **__: FakeRedis())

    def get_pipeline(_: TaskType) -> ScriptedPipeline:
        assert state.pipeline is not None, "the test must script a pipeline"
        return state.pipeline

    async def moderate(self: Any, **_: Any) -> list[dict[str, Any]]:
        return list(state.moderation or [])

    monkeypatch.setattr(processor, "get_pipeline", get_pipeline)
    monkeypatch.setattr(StructuredGenerationService, "moderate", moderate)

    yield state

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_a_successful_job_stores_one_output_one_webhook_and_a_cache_entry(
    harness: Harness,
) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    job_id = await harness.make_job()

    await harness.run(job_id)

    job = await harness.job(job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.completed_at is not None
    assert job.error_code is None
    async with harness.maker() as session:
        output = await session.scalar(select(AIOutput).where(AIOutput.job_id == job_id))
    assert output is not None
    assert output.result_json == QUIZ
    assert output.validation_status == "valid"
    assert (output.input_tokens, output.output_tokens, output.total_tokens) == (120, 80, 200)
    assert float(output.estimated_cost_usd) == pytest.approx(0.0123)
    assert await harness.webhooks(job_id) == 1
    assert await harness.count(CachedAIResult, user_id="user-1") == 1
    assert harness.pipeline.calls == 1


async def test_a_provider_failure_records_failure_without_any_result(harness: Harness) -> None:
    def fail(_: PipelineContext) -> PipelineResult:
        raise ProviderError("upstream 503 body=<student essay>", code="provider_timeout")

    harness.pipeline = ScriptedPipeline(fail)
    job_id = await harness.make_job()

    with pytest.raises(ProviderError):
        await harness.run(job_id)

    job = await harness.job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "provider_timeout"
    assert job.completed_at is not None
    assert await harness.count(AIOutput, job_id=job_id) == 0
    assert await harness.count(CachedAIResult, user_id="user-1") == 0
    # Django must always hear how the job ended, success or not.
    assert await harness.webhooks(job_id) == 1
    # What the status endpoint shows is the reviewed message, never the raw diagnostic.
    view = to_job_view(job)
    assert view.error_message == public_failure_message("provider_timeout")
    assert "student essay" not in (view.error_message or "")


async def test_an_unexpected_exception_still_ends_in_a_recorded_failure(harness: Harness) -> None:
    def explode(_: PipelineContext) -> PipelineResult:
        raise RuntimeError("boom with sk-secret-key and private text")

    harness.pipeline = ScriptedPipeline(explode)
    job_id = await harness.make_job()

    with pytest.raises(RuntimeError):
        await harness.run(job_id)

    job = await harness.job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "RuntimeError"
    assert await harness.count(AIOutput, job_id=job_id) == 0
    assert await harness.webhooks(job_id) == 1
    assert "sk-secret-key" not in (to_job_view(job).error_message or "")


async def test_redelivering_a_finished_job_changes_nothing(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    job_id = await harness.make_job()
    await harness.run(job_id)

    await harness.run(job_id)

    assert harness.pipeline.calls == 1
    assert await harness.count(AIOutput, job_id=job_id) == 1
    assert await harness.webhooks(job_id) == 1


async def test_a_job_another_worker_already_claimed_is_left_alone(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    job_id = await harness.make_job(status=JobStatus.GENERATING)

    await harness.run(job_id)

    assert harness.pipeline.calls == 0
    assert (await harness.job(job_id)).status == JobStatus.GENERATING


async def test_an_unknown_job_is_ignored(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    await harness.run(uuid.uuid4())
    assert harness.pipeline.calls == 0


async def test_a_job_canceled_while_running_stays_canceled_and_pays_for_nothing_stored(
    harness: Harness,
) -> None:
    job_id = await harness.make_job()

    async def cancel_midway(context: PipelineContext) -> PipelineResult:
        async with harness.maker() as other:
            job = await other.get(AIJob, context.job.id)
            assert job is not None
            job.status = JobStatus.CANCELED
            await other.commit()
        return good_result()

    harness.pipeline = ScriptedPipeline(cancel_midway)

    await harness.run(job_id)

    assert (await harness.job(job_id)).status == JobStatus.CANCELED
    assert await harness.count(AIOutput, job_id=job_id) == 0
    assert await harness.count(CachedAIResult, user_id="user-1") == 0


async def test_flagged_content_is_delivered_flagged_and_never_cached(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    harness.moderation = [{"type": "moderation", "category": "harassment"}]
    job_id = await harness.make_job()

    await harness.run(job_id)

    job = await harness.job(job_id)
    assert job.status == JobStatus.COMPLETED
    async with harness.maker() as session:
        output = await session.scalar(select(AIOutput).where(AIOutput.job_id == job_id))
    assert output is not None
    assert output.validation_status == "flagged"
    assert {"type": "moderation", "category": "harassment"} in output.security_flags
    assert await harness.count(CachedAIResult, user_id="user-1") == 0


async def test_an_identical_second_request_is_served_from_the_cache(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    shared = {"input_hash": "f" * 64}
    first = await harness.make_job(**shared)
    await harness.run(first)
    second = await harness.make_job(**shared)

    await harness.run(second)

    assert harness.pipeline.calls == 1, "the second identical request must not call the pipeline"
    async with harness.maker() as session:
        output = await session.scalar(select(AIOutput).where(AIOutput.job_id == second))
    assert output is not None
    assert output.result_cache_hit is True
    assert output.result_json == QUIZ


async def test_the_cache_never_crosses_users(harness: Harness) -> None:
    harness.pipeline = ScriptedPipeline(lambda _: good_result())
    shared = {"input_hash": "e" * 64}
    await harness.run(await harness.make_job(user_id="user-1", **shared))
    other_user = await harness.make_job(user_id="user-2", **shared)

    await harness.run(other_user)

    assert harness.pipeline.calls == 2, "another user must run their own generation"
    async with harness.maker() as session:
        output = await session.scalar(select(AIOutput).where(AIOutput.job_id == other_user))
    assert output is not None
    assert output.result_cache_hit is False


def test_terminal_states_are_exactly_the_three_end_states() -> None:
    assert set(TERMINAL_JOB_STATES) == {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}
