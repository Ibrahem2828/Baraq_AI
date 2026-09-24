from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import Character, DispatchOutboxStatus, JobStatus, TaskType
from app.services.job_recovery import RecoveryAction, recover_stale_jobs, recovery_action
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT


class _Rows:
    def __init__(self, rows: list[AIJob]) -> None:
        self.rows = rows

    def all(self) -> list[AIJob]:
        return self.rows


class _RecoverySession:
    def __init__(
        self, jobs: list[AIJob], existing_event: JobDispatchOutboxEvent | None = None
    ) -> None:
        self.jobs = jobs
        self.existing_event = existing_event
        self.added: list[Any] = []

    async def scalars(self, statement: Any) -> _Rows:
        return _Rows(self.jobs)

    async def scalar(self, statement: Any) -> JobDispatchOutboxEvent | None:
        event = self.existing_event
        self.existing_event = None
        return event

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        return None


def _job(status: JobStatus) -> AIJob:
    job = AIJob(
        user_id="user-1",
        project_id="project-1",
        request_id="00000000-0000-0000-0000-000000000001",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        character=Character.FAHES,
        status=status,
        idempotency_hash="a" * 64,
        input_hash="b" * 64,
        retry_count=0,
    )
    job.created_at = datetime.now(UTC) - timedelta(hours=1)
    job.updated_at = job.created_at
    return job


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.PREPARING])
def test_jobs_that_have_not_started_work_are_safe_to_requeue(status: JobStatus) -> None:
    assert recovery_action(status) == RecoveryAction.REQUEUE


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.RETRIEVING,
        JobStatus.PLANNING,
        JobStatus.GENERATING,
        JobStatus.VALIDATING,
        JobStatus.REPAIRING,
        JobStatus.MATERIALIZING,
    ],
)
def test_paid_or_post_provider_stages_are_never_blindly_replayed(status: JobStatus) -> None:
    assert recovery_action(status) == RecoveryAction.FAIL_UNCERTAIN


@pytest.mark.asyncio
async def test_stale_preparing_job_requeues_existing_dispatch_event() -> None:
    job = _job(JobStatus.PREPARING)
    event = JobDispatchOutboxEvent(
        job_id=job.id,
        request_id=job.request_id,
        event_type="process_ai_job",
        status=DispatchOutboxStatus.DISPATCHED,
    )
    session = _RecoverySession([job], event)

    assert await recover_stale_jobs(
        session, stale_after_seconds=60, max_recoveries=2  # type: ignore[arg-type]
    ) == (1, 0)
    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 1
    assert event.status == DispatchOutboxStatus.PENDING
    assert event.dispatched_at is None


@pytest.mark.asyncio
async def test_new_dispatch_event_routes_to_the_job_task_types_queue() -> None:
    """No existing outbox row (a fresh recovery, not a retry) -- the newly
    created event must be routed via app.models.enums.TASK_QUEUE, not left on
    whatever the column default happens to be."""
    job = _job(JobStatus.PREPARING)
    session = _RecoverySession([job])

    await recover_stale_jobs(session, stale_after_seconds=60, max_recoveries=2)  # type: ignore[arg-type]

    assert len(session.added) == 1
    assert session.added[0].target_queue == "ai_interactive"


@pytest.mark.asyncio
async def test_new_dispatch_event_for_a_sada_job_routes_to_the_audio_queue() -> None:
    job = _job(JobStatus.PREPARING)
    job.task_type = TaskType.SADA_TRANSCRIBE_AUDIO
    session = _RecoverySession([job])

    await recover_stale_jobs(session, stale_after_seconds=60, max_recoveries=2)  # type: ignore[arg-type]

    assert len(session.added) == 1
    assert session.added[0].target_queue == "ai_audio"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.RETRIEVING, JobStatus.VALIDATING])
async def test_stale_uncertain_job_fails_and_enqueues_delivery(status: JobStatus) -> None:
    job = _job(status)
    session = _RecoverySession([job])

    assert await recover_stale_jobs(
        session, stale_after_seconds=60, max_recoveries=2  # type: ignore[arg-type]
    ) == (0, 1)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "worker_interrupted_execution_uncertain"
    assert len(session.added) == 1
    assert session.added[0].event_type == RESULT_WEBHOOK_EVENT


def _process_event(job: AIJob, status: DispatchOutboxStatus) -> JobDispatchOutboxEvent:
    return JobDispatchOutboxEvent(
        job_id=job.id, request_id=job.request_id, event_type="process_ai_job", status=status
    )


@pytest.mark.asyncio
async def test_queued_job_whose_dispatch_was_lost_is_rearmed() -> None:
    """Production 2026-09-24: the worker raised before PREPARING committed, so
    the job stayed QUEUED with its event DISPATCHED and nothing ever ran it."""
    job = _job(JobStatus.QUEUED)
    event = _process_event(job, DispatchOutboxStatus.DISPATCHED)
    session = _RecoverySession([job], event)

    assert await recover_stale_jobs(
        session, stale_after_seconds=60, max_recoveries=2  # type: ignore[arg-type]
    ) == (1, 0)
    assert job.status == JobStatus.QUEUED
    assert job.retry_count == 1
    assert event.status == DispatchOutboxStatus.PENDING
    assert event.dispatched_at is None
    assert session.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [DispatchOutboxStatus.PENDING, DispatchOutboxStatus.DISPATCHING])
async def test_queued_job_still_owned_by_the_outbox_is_left_alone(
    status: DispatchOutboxStatus,
) -> None:
    job = _job(JobStatus.QUEUED)
    event = _process_event(job, status)
    session = _RecoverySession([job], event)

    assert await recover_stale_jobs(
        session, stale_after_seconds=60, max_recoveries=2  # type: ignore[arg-type]
    ) == (0, 0)
    assert job.retry_count == 0
    assert event.status == status


@pytest.mark.asyncio
async def test_queued_job_is_never_failed_when_recoveries_run_out() -> None:
    """It may just be waiting behind a long queue; failing it would discard a
    job that could still run."""
    job = _job(JobStatus.QUEUED)
    job.retry_count = 2
    event = _process_event(job, DispatchOutboxStatus.DISPATCHED)
    session = _RecoverySession([job], event)

    assert await recover_stale_jobs(
        session, stale_after_seconds=60, max_recoveries=2  # type: ignore[arg-type]
    ) == (0, 0)
    assert job.status == JobStatus.QUEUED
    assert event.status == DispatchOutboxStatus.DISPATCHED
