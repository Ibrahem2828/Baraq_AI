from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob
from app.models.enums import JobStatus
from app.services.job_processor import process_job
from app.workers.celery_app import celery_app


@celery_app.task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    name="app.workers.tasks.process_ai_job",
)
def process_ai_job(self, job_id: str) -> None:  # noqa: ANN001
    asyncio.run(process_job(job_id))


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
        return int(result.rowcount or 0)


@celery_app.task(name="app.workers.tasks.cleanup_expired_data")
def cleanup_expired_data() -> int:
    return asyncio.run(_cleanup())
