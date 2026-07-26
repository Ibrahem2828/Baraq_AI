from __future__ import annotations

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.telemetry import AI_LATENCY, AI_REQUESTS
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob, AIOutput
from app.models.enums import JobStatus
from app.pipelines.base import PipelineContext
from app.pipelines.registry import get_pipeline
from app.providers.router import ProviderRouter
from app.rag.embeddings import EmbeddingService
from app.services.backend_client import BackendClient
from app.services.generation import StructuredGenerationService
from app.services.source_ingestion import SourceIngestionService

logger = get_logger(__name__)


async def _update_progress(
    session: AsyncSession,
    job: AIJob,
    *,
    status: JobStatus,
    percent: int,
    message: str,
) -> None:
    job.status = status
    job.progress_percent = percent
    job.progress_message = message
    if job.started_at is None:
        job.started_at = datetime.now(UTC)
    await session.commit()


async def process_job(job_id: str) -> None:
    settings = get_settings()
    started = datetime.now(UTC)
    backend = BackendClient()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with AsyncSessionLocal() as session:
            job = await session.scalar(
                select(AIJob)
                .where(AIJob.id == uuid.UUID(job_id))
                .options(selectinload(AIJob.output), selectinload(AIJob.attempts))
            )
            if not job:
                logger.error("ai_job_not_found", job_id=job_id)
                return
            if job.status == JobStatus.CANCELED:
                return
            if job.output is not None or job.status == JobStatus.COMPLETED:
                return
            await _update_progress(
                session,
                job,
                status=JobStatus.PROCESSING,
                percent=5,
                message="بدأت معالجة الطلب",
            )
            router = ProviderRouter(redis)
            generation = StructuredGenerationService(session=session, router=router)
            embeddings = EmbeddingService(router)
            ingestion = SourceIngestionService(
                session=session,
                backend=backend,
                embeddings=embeddings,
            )
            pipeline = get_pipeline(job.task_type)
            await _update_progress(
                session,
                job,
                status=JobStatus.RETRIEVING,
                percent=20,
                message="يتم تجهيز البيانات والمصادر",
            )
            result = await pipeline.execute(
                PipelineContext(
                    session=session,
                    job=job,
                    backend=backend,
                    generation=generation,
                    ingestion=ingestion,
                )
            )
            if job.status == JobStatus.CANCELED:
                return
            await _update_progress(
                session,
                job,
                status=JobStatus.VALIDATING,
                percent=80,
                message="يتم التحقق من جودة النتيجة",
            )
            provider = result.provider_result
            output = AIOutput(
                job_id=job.id,
                result_json=result.result_json,
                citations=[item.model_dump(mode="json") for item in result.citations],
                validation_status="valid",
                quality_score=result.quality_score,
                groundedness_score=result.groundedness_score,
                provider_account=provider.account,
                provider_response_id=provider.response_id,
                model_name=provider.model,
                input_tokens=provider.usage.input_tokens,
                output_tokens=provider.usage.output_tokens,
                total_tokens=provider.usage.total_tokens,
                estimated_cost_usd=provider.estimated_cost_usd,
                provider_latency_ms=provider.latency_ms,
            )
            session.add(output)
            await session.flush()
            # Django materializes results after it receives a Phase 4 outbox webhook.
            # No direct callback is emitted here because callbacks must be persisted first.
            job.status = JobStatus.COMPLETED
            job.progress_percent = 100
            job.progress_message = "اكتملت المعالجة بنجاح"
            job.completed_at = datetime.now(UTC)
            await session.commit()
            AI_REQUESTS.labels(job.task_type.value, "completed").inc()
            AI_LATENCY.labels(job.task_type.value).observe(
                (job.completed_at - started).total_seconds()
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ai_job_failed", job_id=job_id, error=str(exc))
        async with AsyncSessionLocal() as session:
            job = await session.scalar(select(AIJob).where(AIJob.id == uuid.UUID(job_id)))
            if job and job.status != JobStatus.CANCELED:
                job.status = JobStatus.FAILED
                job.progress_message = "فشلت معالجة الطلب"
                job.error_code = exc.code if isinstance(exc, AppError) else exc.__class__.__name__
                job.error_message = str(exc)[:4000]
                job.completed_at = datetime.now(UTC)
                await session.commit()
                AI_REQUESTS.labels(job.task_type.value, "failed").inc()
        raise
    finally:
        await backend.aclose()
        await redis.aclose()
