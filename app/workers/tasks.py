from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import JobStatus
from app.services.job_processor import process_job
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
        return celery_app.send_task("app.workers.tasks.process_ai_job", args=[str(event.job_id)])
    if event.event_type == RESULT_WEBHOOK_EVENT:
        return celery_app.send_task(
            "app.workers.tasks.deliver_result_webhook", args=[str(event.id)]
        )
    raise ValueError(f"Unsupported outbox event type: {event.event_type}")


@celery_task(name="app.workers.tasks.dispatch_job_outbox")
def dispatch_job_outbox() -> int:
    """The durable retry loop for post-commit job delivery."""
    return asyncio.run(OutboxDispatcher().dispatch_pending(_enqueue_outbox_event))


async def _cleanup() -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_retention_days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(AIJob).where(
                AIJob.created_at < cutoff,
                AIJob.status.in_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED]),
            )
        )
        await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)


@celery_task(name="app.workers.tasks.cleanup_expired_data")
def cleanup_expired_data() -> int:
    return asyncio.run(_cleanup())
