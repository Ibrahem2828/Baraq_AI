from __future__ import annotations

from fastapi import APIRouter, Request, status
from redis.exceptions import RedisError

from app.api.dependencies import DbSession, DjangoService, IdempotencyKey, RedisClient
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import enforce_rate_limit
from app.models.ai_job import AIJob
from app.models.enums import TaskType
from app.schemas.common import APIEnvelope, Citation
from app.schemas.jobs import (
    DjangoJobCreateRequestV2,
    JobAccepted,
    JobOutputView,
    JobView,
)
from app.services.backend_client import BackendClient
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["Django Gateway Jobs"])
logger = get_logger(__name__)


def to_job_view(job: AIJob) -> JobView:
    output_view = None
    if job.output:
        output_view = JobOutputView(
            output_id=job.output.id,
            result=job.output.result_json,
            citations=[Citation.model_validate(item) for item in job.output.citations],
            validation_status=job.output.validation_status,
            quality_score=job.output.quality_score,
            groundedness_score=job.output.groundedness_score,
            model_name=job.output.model_name,
            provider_account=job.output.provider_account.value,
            input_tokens=job.output.input_tokens,
            output_tokens=job.output.output_tokens,
            total_tokens=job.output.total_tokens,
            estimated_cost_usd=job.output.estimated_cost_usd,
            warnings=job.output.warnings,
            security_flags=job.output.security_flags,
            validation_report=job.output.validation_report,
        )
    return JobView(
        job_id=job.backend_request_id or str(job.id),
        ai_job_id=job.id,
        request_id=job.request_id,
        task_type=job.task_type,
        character=job.character,
        status=job.status,
        stage=job.status,
        progress_percent=job.progress_percent,
        progress_message=job.progress_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_code=job.error_code,
        error_message=job.error_message,
        output=output_view,
    )


@router.post("", response_model=APIEnvelope[JobAccepted], status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    payload: DjangoJobCreateRequestV2,
    session: DbSession,
    _: DjangoService,
    redis: RedisClient,
    http_request: Request,
    idempotency_key: IdempotencyKey = None,
) -> APIEnvelope[JobAccepted]:
    settings = get_settings()
    try:
        await enforce_rate_limit(
            redis=redis,
            key=f"user:{payload.user_id}",
            limit_per_minute=settings.ai_requests_per_minute,
        )
    except RedisError:
        # The rate limiter is a best-effort control on top of the budget and
        # circuit-breaker safety nets that already gate every provider call;
        # a Redis outage must not take down job creation with it.
        logger.warning("ai_rate_limit_check_unavailable", user_id=payload.user_id)
    request = payload.to_internal()
    # The contract trace is the canonical request identity across FastAPI,
    # persistence, Celery, provider attempts and the result webhook.
    http_request.state.request_id = str(request.trace.request_id)
    # Task input validation is deliberately completed at the HTTP boundary,
    # before a job or outbox record can be created.
    normalized_input = JobService.validate_payload(request.task_type, request.input)
    request = request.model_copy(update={"input": normalized_input})
    service = JobService(session)
    existing = await service.existing_idempotency_result(user_id=payload.user_id, request=request)
    if existing is not None:
        job, cache_hit = existing
    else:
        backend = BackendClient()
        try:
            source_versions = await JobService.freeze_source_versions(
                user_id=payload.user_id,
                project_id=request.project_id,
                task_type=request.task_type,
                payload=normalized_input,
                backend=backend,
            )
        finally:
            await backend.aclose()
        job, cache_hit = await service.create_job(
            user_id=payload.user_id,
            request=request,
            source_versions=source_versions,
        )
    logger.info(
        "ai_job_accepted",
        request_id=job.request_id,
        ai_job_id=str(job.id),
        task_type=job.task_type.value,
        cache_hit=cache_hit,
    )
    job_id = job.backend_request_id or str(job.id)
    return APIEnvelope(
        data=JobAccepted(
            job_id=job_id,
            ai_job_id=job.id,
            status=job.status,
            task_type=job.task_type,
            character=job.character,
            status_url=f"{settings.public_api_prefix}/jobs/{job_id}",
            estimated_wait_seconds=(30 if job.task_type == TaskType.SADA_TRANSCRIBE_AUDIO else 5),
            cache_hit=cache_hit,
        )
    )


@router.get("/{job_id}", response_model=APIEnvelope[JobView])
async def get_job(
    job_id: str,
    user_id: str,
    session: DbSession,
    _: DjangoService,
) -> APIEnvelope[JobView]:
    job = await JobService(session).get_job_by_client_id(client_job_id=job_id, user_id=user_id)
    return APIEnvelope(data=to_job_view(job))


@router.post("/{job_id}/cancel", response_model=APIEnvelope[JobView])
async def cancel_job(
    job_id: str,
    user_id: str,
    session: DbSession,
    _: DjangoService,
) -> APIEnvelope[JobView]:
    job = await JobService(session).cancel_job_by_client_id(client_job_id=job_id, user_id=user_id)
    return APIEnvelope(data=to_job_view(job))
