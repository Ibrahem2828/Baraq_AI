from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.dependencies import Backend, CurrentUser, DbSession, RateLimitedUser
from app.core.config import get_settings
from app.models.ai_job import AIJob
from app.schemas.common import APIEnvelope, Citation
from app.schemas.jobs import JobAccepted, JobCreateRequest, JobOutputView, JobView
from app.services.job_service import JobService

router = APIRouter(prefix="/jobs", tags=["AI Jobs"])


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
            materialized_resource_type=job.output.materialized_resource_type,
            materialized_resource_id=job.output.materialized_resource_id,
        )
    return JobView(
        job_id=job.id,
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
    payload: JobCreateRequest,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    service = JobService(session, backend)
    job, cache_hit = await service.create_job(user_id=user.user_id, request=payload)
    settings = get_settings()
    return APIEnvelope(
        data=JobAccepted(
            job_id=job.id,
            status=job.status,
            task_type=job.task_type,
            character=job.character,
            status_url=f"{settings.public_api_prefix}/jobs/{job.id}",
            estimated_wait_seconds=5 if job.task_type.value != "sada_transcribe" else 30,
            cache_hit=cache_hit,
        )
    )


@router.get("", response_model=APIEnvelope[list[JobView]])
async def list_jobs(
    session: DbSession,
    user: CurrentUser,
    backend: Backend,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> APIEnvelope[list[JobView]]:
    jobs = await JobService(session, backend).list_jobs(
        user_id=user.user_id, limit=limit, offset=offset
    )
    return APIEnvelope(data=[to_job_view(job) for job in jobs])


@router.get("/{job_id}", response_model=APIEnvelope[JobView])
async def get_job(
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    backend: Backend,
) -> APIEnvelope[JobView]:
    job = await JobService(session, backend).get_job(job_id=job_id, user_id=user.user_id)
    return APIEnvelope(data=to_job_view(job))


@router.post("/{job_id}/cancel", response_model=APIEnvelope[JobView])
async def cancel_job(
    job_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    backend: Backend,
) -> APIEnvelope[JobView]:
    job = await JobService(session, backend).cancel_job(job_id=job_id, user_id=user.user_id)
    return APIEnvelope(data=to_job_view(job))
