from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.telemetry import AI_LATENCY, AI_REQUESTS
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob, AIOutput, JobDispatchOutboxEvent
from app.models.enums import JobStatus
from app.pipelines.base import PipelineContext, PipelineResult, require_project_id
from app.pipelines.registry import get_pipeline
from app.prompts.registry import get_prompt_registry
from app.providers.base import ProviderResult, ProviderUsage
from app.providers.router import ProviderRouter
from app.rag.embeddings import EmbeddingService
from app.schemas.common import Citation
from app.services.backend_client import BackendClient
from app.services.generation import StructuredGenerationService
from app.services.job_recovery import ensure_result_delivery
from app.services.job_state_machine import TERMINAL_JOB_STATES, JobStateMachine
from app.services.result_cache import ResultCacheService, cache_hit_metadata, compute_fingerprint
from app.services.routing_config import get_routing_config
from app.services.source_ingestion import SourceIngestionService
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT

logger = get_logger(__name__)


async def _locked_job(session: AsyncSession, job_id: str) -> AIJob | None:
    return cast(
        AIJob | None,
        await session.scalar(
            select(AIJob)
            .where(AIJob.id == uuid.UUID(job_id))
            .options(selectinload(AIJob.output), selectinload(AIJob.attempts))
            .with_for_update()
        ),
    )


async def _transition(
    session: AsyncSession, job: AIJob, *, status: JobStatus, message: str
) -> None:
    JobStateMachine.transition(job, target=status, message=message)
    if job.started_at is None:
        job.started_at = datetime.now(UTC)
    await session.commit()


async def _cancellation_requested(session: AsyncSession, job_id: str) -> bool:
    current_status = await session.scalar(select(AIJob.status).where(AIJob.id == uuid.UUID(job_id)))
    return current_status == JobStatus.CANCELED


def _pipeline_result_from_cache(cached: Any) -> PipelineResult:
    """Reconstructs a PipelineResult from a result-cache hit. The provider
    usage/cost here is deliberately zeroed -- no real call happened this
    time -- while `source_model_name`/`source_provider_account` (kept for
    provenance) still say which real call originally produced this content."""
    return PipelineResult(
        result_json=cached.result_json,
        citations=[Citation.model_validate(item) for item in cached.citations],
        provider_result=ProviderResult(
            data=cached.result_json,
            account=cached.source_provider_account,
            model=cached.source_model_name,
            response_id=None,
            usage=ProviderUsage(),
            estimated_cost_usd=0.0,
            metadata=cache_hit_metadata(cached),
        ),
        quality_score=cached.quality_score,
        groundedness_score=cached.groundedness_score,
        warnings=list(cached.warnings),
        security_flags=list(cached.security_flags),
    )


def _extract_texts(value: Any, *, out: list[str] | None = None) -> list[str]:
    """Collect leaf string values out of a pipeline result payload so they
    can be screened by moderation, without needing every pipeline to know
    about moderation itself."""
    out = [] if out is None else out
    if isinstance(value, str):
        if value.strip():
            out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _extract_texts(item, out=out)
    elif isinstance(value, list):
        for item in value:
            _extract_texts(item, out=out)
    return out


async def process_job(job_id: str) -> None:
    """Process one job safely under at-least-once Celery delivery semantics."""
    settings = get_settings()
    started = datetime.now(UTC)
    backend = BackendClient()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    request_id: str | None = None
    try:
        async with AsyncSessionLocal() as session:
            job = await _locked_job(session, job_id)
            if job is None:
                logger.error("ai_job_not_found", job_id=job_id)
                return
            request_id = job.request_id
            logger.info("ai_job_processing", job_id=job_id, request_id=request_id)
            if job.status != JobStatus.QUEUED:
                # Only one worker may atomically claim queued work.
                return
            project_id = require_project_id(job)
            await _transition(
                session, job, status=JobStatus.PREPARING, message="Preparing job for processing"
            )

            router = ProviderRouter(redis)
            generation = StructuredGenerationService(session=session, router=router)
            embeddings = EmbeddingService(router, session=session)
            ingestion = SourceIngestionService(
                session=session,
                backend=backend,
                embeddings=embeddings,
            )
            pipeline = get_pipeline(job.task_type)
            job.pipeline_version = pipeline.version

            # Cacheable task types: resolve the prompt/routing up front (the
            # same static, task_type-only lookup each pipeline does inside
            # execute()) so a fingerprint can be checked *before* paying for
            # generation. Non-cacheable types (e.g. Khota -- see
            # ResultCacheService's docstring) skip this and always execute.
            fingerprint: str | None = None
            if job.task_type in ResultCacheService.CACHEABLE_TASK_TYPES:
                routing = get_routing_config().get(job.task_type.value)
                prompt = get_prompt_registry().get(routing.prompt) if routing.prompt else None
                if prompt is not None:
                    job.prompt_name = prompt.name
                    job.prompt_version = prompt.version
                    job.prompt_checksum = prompt.checksum
                    fingerprint = compute_fingerprint(
                        user_id=job.user_id,
                        project_id=project_id,
                        task_type=job.task_type.value,
                        input_hash=job.input_hash,
                        source_versions=job.source_versions,
                        prompt_checksum=prompt.checksum,
                        pipeline_version=pipeline.version,
                    )
            cache = ResultCacheService(session)
            cached = (
                await cache.lookup(
                    fingerprint, user_id=job.user_id, project_id=project_id
                )
                if fingerprint
                else None
            )

            await _transition(
                session,
                job,
                status=JobStatus.RETRIEVING,
                message="Retrieving authoritative data and sources",
            )
            if cached is not None:
                result = _pipeline_result_from_cache(cached)
            else:
                result = await pipeline.execute(
                    PipelineContext(
                        session=session,
                        job=job,
                        backend=backend,
                        generation=generation,
                        ingestion=ingestion,
                    )
                )
            if await _cancellation_requested(session, job_id):
                return
            # The pipeline owns retrieval/generation.  This persisted stage is
            # where its result is independently validated.
            await _transition(
                session, job, status=JobStatus.VALIDATING, message="Validating generated result"
            )
            provider = result.provider_result
            locked_job = await _locked_job(session, job_id)
            if locked_job is None or locked_job.status == JobStatus.CANCELED:
                return
            if locked_job.output is not None or locked_job.status == JobStatus.COMPLETED:
                return
            if cached is not None:
                # Identical content already passed moderation once; a cache
                # hit reuses that verdict instead of re-screening.
                moderation_flags: list[dict[str, Any]] = []
            else:
                moderation_flags = await generation.moderate(
                    routing_key=f"{locked_job.id}:moderation",
                    texts=_extract_texts(result.result_json),
                )
            security_flags = [*result.security_flags, *moderation_flags]
            output = AIOutput(
                job_id=locked_job.id,
                result_json=result.result_json,
                citations=[item.model_dump(mode="json") for item in result.citations],
                validation_status="flagged" if moderation_flags else "valid",
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
                request_id=locked_job.request_id,
                warnings=result.warnings,
                security_flags=security_flags,
                result_cache_hit=cached is not None,
                validation_report={
                    "status": "flagged" if moderation_flags else "valid",
                    "pipeline": locked_job.task_type.value,
                    "pipeline_version": locked_job.pipeline_version,
                    "knowledge_policy": pipeline.knowledge_policy.value,
                    "prompt_checksum": locked_job.prompt_checksum,
                    "source_versions": locked_job.source_versions,
                    "result_cache_hit": cached is not None,
                },
            )
            session.add(output)
            session.add(
                JobDispatchOutboxEvent(
                    job_id=locked_job.id,
                    event_type=RESULT_WEBHOOK_EVENT,
                    request_id=locked_job.request_id,
                )
            )
            JobStateMachine.transition(
                locked_job, target=JobStatus.COMPLETED, message="Job completed"
            )
            locked_job.completed_at = datetime.now(UTC)
            await session.commit()
            AI_REQUESTS.labels(job.task_type.value, "completed").inc()
            AI_LATENCY.labels(job.task_type.value).observe(
                (datetime.now(UTC) - started).total_seconds()
            )
            if fingerprint is not None and cached is None and not moderation_flags:
                # Only a fresh, unflagged generation is worth caching -- and
                # only once the job is confirmed COMPLETED and committed.
                await cache.store(
                    fingerprint=fingerprint,
                    user_id=job.user_id,
                    project_id=project_id,
                    task_type=job.task_type,
                    result=result,
                )
    except Exception as exc:
        logger.exception("ai_job_failed", job_id=job_id, request_id=request_id, error=str(exc))
        async with AsyncSessionLocal() as session:
            job = await _locked_job(session, job_id)
            if job is not None and job.status not in TERMINAL_JOB_STATES:
                JobStateMachine.transition(
                    job, target=JobStatus.FAILED, message="Job processing failed"
                )
                job.error_code = exc.code if isinstance(exc, AppError) else exc.__class__.__name__
                job.error_message = str(exc)[:4000]
                job.completed_at = datetime.now(UTC)
                await ensure_result_delivery(session, job)
                await session.commit()
                AI_REQUESTS.labels(job.task_type.value, "failed").inc()
        raise
    finally:
        await backend.aclose()
        await redis.aclose()
