from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import TASK_QUEUE, DispatchOutboxStatus, JobStatus
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT

PROCESS_JOB_EVENT = "process_ai_job"


class RecoveryAction(StrEnum):
    REQUEUE = "requeue"
    FAIL_UNCERTAIN = "fail_uncertain"


def recovery_action(status: JobStatus) -> RecoveryAction:
    # QUEUED and PREPARING are both persisted before source retrieval or
    # provider work starts. Every later processing state may already have
    # incurred a paid call whose response was lost with the worker, so
    # replaying it blindly is unsafe.
    if status in (JobStatus.QUEUED, JobStatus.PREPARING):
        return RecoveryAction.REQUEUE
    return RecoveryAction.FAIL_UNCERTAIN


async def _event(
    session: AsyncSession, job: AIJob, event_type: str
) -> JobDispatchOutboxEvent:
    existing = await session.scalar(
        select(JobDispatchOutboxEvent)
        .where(
            JobDispatchOutboxEvent.job_id == job.id,
            JobDispatchOutboxEvent.event_type == event_type,
        )
        .with_for_update()
    )
    if existing is not None:
        return existing
    target_queue = (
        TASK_QUEUE[job.task_type] if event_type == PROCESS_JOB_EVENT else "ai_interactive"
    )
    created = JobDispatchOutboxEvent(
        job_id=job.id, event_type=event_type, request_id=job.request_id, target_queue=target_queue
    )
    session.add(created)
    return created


async def ensure_result_delivery(session: AsyncSession, job: AIJob) -> None:
    """Persist one final webhook event without rerunning any AI operation."""
    await _event(session, job, RESULT_WEBHOOK_EVENT)


async def recover_stale_jobs(
    session: AsyncSession, *, stale_after_seconds: int, max_recoveries: int
) -> tuple[int, int]:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
    processing = [
        JobStatus.QUEUED,
        JobStatus.PREPARING,
        JobStatus.RETRIEVING,
        JobStatus.PLANNING,
        JobStatus.GENERATING,
        JobStatus.VALIDATING,
        JobStatus.REPAIRING,
        JobStatus.MATERIALIZING,
    ]
    jobs = list(
        (
            await session.scalars(
                select(AIJob)
                .where(AIJob.status.in_(processing), AIJob.updated_at < cutoff)
                .order_by(AIJob.updated_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    requeued = failed = 0
    for job in jobs:
        existing: JobDispatchOutboxEvent | None = None
        if job.status == JobStatus.QUEUED:
            # A queued job whose dispatch was already handed to the broker but
            # never reached PREPARING was orphaned: the worker died (or raised)
            # before its first transition committed, or the message was lost.
            # While its event is still pending the outbox owns it. It is never
            # failed here -- it may simply be waiting behind a long queue, and
            # a duplicate delivery is harmless because the processor only
            # starts a job that is still QUEUED under a row lock.
            existing = await session.scalar(
                select(JobDispatchOutboxEvent)
                .where(
                    JobDispatchOutboxEvent.job_id == job.id,
                    JobDispatchOutboxEvent.event_type == PROCESS_JOB_EVENT,
                )
                .with_for_update()
            )
            if existing is not None and existing.status != DispatchOutboxStatus.DISPATCHED:
                continue
            if job.retry_count >= max_recoveries:
                continue
        action = recovery_action(job.status)
        if action == RecoveryAction.REQUEUE and job.retry_count < max_recoveries:
            job.status = JobStatus.QUEUED
            job.retry_count += 1
            job.progress_message = "Recovered after worker interruption"
            dispatch = existing or await _event(session, job, PROCESS_JOB_EVENT)
            dispatch.status = DispatchOutboxStatus.PENDING
            dispatch.available_at = datetime.now(UTC)
            dispatch.locked_at = None
            dispatch.last_error = None
            dispatch.dispatched_at = None
            requeued += 1
            continue

        job.status = JobStatus.FAILED
        job.error_code = "worker_interrupted_execution_uncertain"
        job.error_message = "Worker stopped after processing began; provider replay was suppressed"
        job.progress_message = "Job failed safely after worker interruption"
        job.completed_at = datetime.now(UTC)
        delivery = await _event(session, job, RESULT_WEBHOOK_EVENT)
        delivery.status = DispatchOutboxStatus.PENDING
        delivery.available_at = datetime.now(UTC)
        delivery.locked_at = None
        delivery.last_error = None
        delivery.dispatched_at = None
        failed += 1
    await session.commit()
    return requeued, failed
