from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import CandidateStatus, JobStatus, SourceStatus
from app.models.feedback import TrainingDatasetCandidate
from app.models.source import SourceChunk, SourceDocument
from app.services.job_processor import process_job
from app.services.job_recovery import recover_stale_jobs
from app.services.outbox_dispatcher import OutboxDispatcher
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT, deliver_result_webhook
from app.workers.celery_app import celery_app
from app.workers.celery_compat import celery_task


@celery_task(name="app.workers.tasks.process_ai_job")
def process_ai_job(job_id: str) -> None:
    asyncio.run(process_job(job_id))


@celery_task(name="app.workers.tasks.deliver_result_webhook")
def deliver_result_webhook_task(event_id: str) -> None:
    asyncio.run(deliver_result_webhook(event_id))


def _enqueue_outbox_event(event: JobDispatchOutboxEvent) -> object:
    if event.event_type == "process_ai_job":
        # Routed per-job (ai_interactive vs ai_audio) via the queue recorded
        # on the event at creation time -- see app.models.enums.TASK_QUEUE.
        return celery_app.send_task(
            "app.workers.tasks.process_ai_job", args=[str(event.job_id)], queue=event.target_queue
        )
    if event.event_type == RESULT_WEBHOOK_EVENT:
        return celery_app.send_task(
            "app.workers.tasks.deliver_result_webhook", args=[str(event.id)]
        )
    raise ValueError(f"Unsupported outbox event type: {event.event_type}")


@celery_task(name="app.workers.tasks.dispatch_job_outbox")
def dispatch_job_outbox() -> int:
    """The durable retry loop for post-commit job delivery."""
    return asyncio.run(OutboxDispatcher().dispatch_pending(_enqueue_outbox_event))


async def _recover_stale_jobs() -> tuple[int, int]:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        return await recover_stale_jobs(
            session,
            stale_after_seconds=settings.job_stale_after_seconds,
            max_recoveries=settings.job_max_recoveries,
        )


@celery_task(name="app.workers.tasks.recover_stale_ai_jobs")
def recover_stale_ai_jobs() -> tuple[int, int]:
    return asyncio.run(_recover_stale_jobs())


async def _cleanup() -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    job_cutoff = now - timedelta(days=settings.job_retention_days)
    raw_cutoff = now - timedelta(days=settings.raw_content_retention_days)
    training_cutoff = now - timedelta(days=settings.training_candidate_retention_days)
    async with AsyncSessionLocal() as session:
        jobs = await session.execute(
            delete(AIJob).where(
                AIJob.created_at < job_cutoff,
                AIJob.status.in_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED]),
            )
        )
        expired_document_ids = select(SourceDocument.id).where(
            SourceDocument.updated_at < raw_cutoff,
            SourceDocument.status == SourceStatus.READY,
        )
        chunks = await session.execute(
            delete(SourceChunk).where(SourceChunk.document_id.in_(expired_document_ids))
        )
        documents = await session.execute(
            update(SourceDocument)
            .where(SourceDocument.id.in_(expired_document_ids))
            .values(
                status=SourceStatus.PENDING,
                extraction_error="Raw content purged by retention policy; re-ingestion required",
            )
        )
        candidates = await session.execute(
            delete(TrainingDatasetCandidate).where(
                TrainingDatasetCandidate.updated_at < training_cutoff,
                TrainingDatasetCandidate.status.in_(
                    [CandidateStatus.REJECTED, CandidateStatus.EXPORTED]
                ),
            )
        )
        await session.commit()
        return {
            "jobs": int(getattr(jobs, "rowcount", 0) or 0),
            "chunks": int(getattr(chunks, "rowcount", 0) or 0),
            "documents": int(getattr(documents, "rowcount", 0) or 0),
            "training_candidates": int(getattr(candidates, "rowcount", 0) or 0),
        }


@celery_task(name="app.workers.tasks.cleanup_expired_data")
def cleanup_expired_data() -> dict[str, int]:
    return asyncio.run(_cleanup())
