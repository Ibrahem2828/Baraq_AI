from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import DjangoService, DbSession, IdempotencyKey
from app.core.config import get_settings
from app.models.ai_job import AIJob
from app.models.enums import TaskType
from app.schemas.common import APIEnvelope, Citation
from app.schemas.jobs import (
    DjangoJobCreateRequest,
    JobAccepted,
    JobCreateRequest,
    JobOutputView,
    JobView,
)
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["Django Gateway Jobs"])


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
        )
    return JobView(
        job_id=job.backend_request_id or str(job.id),
        ai_job_id=job.id,
        task_type=job.task_type,
        character=job.character,
        status=job.status,
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
    payload: DjangoJobCreateRequest,
    session: DbSession,
    idempotency_key: IdempotencyKey,
    _: DjangoService,
) -> APIEnvelope[JobAccepted]:
    request = JobCreateRequest(
        task_type=payload.task_type,
        payload=payload.input,
        idempotency_key=idempotency_key,
        backend_request_id=payload.client_job_id,
        model_tier=payload.model_tier,
    )
    job, cache_hit = await JobService(session).create_job(user_id=payload.user_id, request=request)
    settings = get_settings()
    job_id = job.backend_request_id or str(job.id)
    return APIEnvelope(
        data=JobAccepted(
            job_id=job_id,
            ai_job_id=job.id,
            status=job.status,
            task_type=job.task_type,
            character=job.character,
            status_url=f"{settings.public_api_prefix}/jobs/{job_id}",
            estimated_wait_seconds=(
                30 if job.task_type == TaskType.SADA_TRANSCRIBE_AUDIO else 5
            ),
            cache_hit=cache_hit,
        )
    )


@router.get("/{job_id}", response_model=APIEnvelope[JobView])
async def get_job(
    job_id: str,
    session: DbSession,
    _: DjangoService,
) -> APIEnvelope[JobView]:
    job = await JobService(session).get_job_by_client_id(client_job_id=job_id)
    return APIEnvelope(data=to_job_view(job))


@router.post("/{job_id}/cancel", response_model=APIEnvelope[JobView])
async def cancel_job(
    job_id: str,
    session: DbSession,
    _: DjangoService,
) -> APIEnvelope[JobView]:
    job = await JobService(session).cancel_job_by_client_id(client_job_id=job_id)
    return APIEnvelope(data=to_job_view(job))
