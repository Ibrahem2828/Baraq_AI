from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import Backend, DbSession, IdempotencyKey, RateLimitedUser
from app.models.enums import TaskType
from app.schemas.common import APIEnvelope
from app.schemas.fahes import FahesRequest
from app.schemas.jobs import JobAccepted, JobCreateRequest
from app.schemas.kholasa import KholasaRequest
from app.schemas.khota import KhotaRequest
from app.schemas.rasheed import RasheedRequest
from app.schemas.sada import SadaRequest
from app.services.job_service import JobService

router = APIRouter(tags=["Baraq Characters"])


async def _create(
    *,
    task_type: TaskType,
    payload: object,
    idempotency_key: str,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    request = JobCreateRequest(
        task_type=task_type,
        payload=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    job, cache_hit = await JobService(session, backend).create_job(
        user_id=user.user_id,
        request=request,
    )
    return APIEnvelope(
        data=JobAccepted(
            job_id=job.id,
            status=job.status,
            task_type=job.task_type,
            character=job.character,
            status_url=f"/api/ai/v1/jobs/{job.id}",
            estimated_wait_seconds=30 if task_type == TaskType.SADA_TRANSCRIBE else 5,
            cache_hit=cache_hit,
        )
    )


@router.post(
    "/fahes/quizzes",
    response_model=APIEnvelope[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def fahes_quiz(
    payload: FahesRequest,
    idempotency_key: IdempotencyKey,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    return await _create(
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        payload=payload,
        idempotency_key=idempotency_key,
        session=session,
        user=user,
        backend=backend,
    )


@router.post(
    "/khota/plans",
    response_model=APIEnvelope[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def khota_plan(
    payload: KhotaRequest,
    idempotency_key: IdempotencyKey,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    return await _create(
        task_type=TaskType.KHOTA_GENERATE_PLAN,
        payload=payload,
        idempotency_key=idempotency_key,
        session=session,
        user=user,
        backend=backend,
    )


@router.post(
    "/rasheed/recommendations",
    response_model=APIEnvelope[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def rasheed_recommendations(
    payload: RasheedRequest,
    idempotency_key: IdempotencyKey,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    return await _create(
        task_type=TaskType.RASHEED_RECOMMEND,
        payload=payload,
        idempotency_key=idempotency_key,
        session=session,
        user=user,
        backend=backend,
    )


@router.post(
    "/kholasa/summaries",
    response_model=APIEnvelope[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def kholasa_summary(
    payload: KholasaRequest,
    idempotency_key: IdempotencyKey,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    return await _create(
        task_type=TaskType.KHOLASA_SUMMARIZE,
        payload=payload,
        idempotency_key=idempotency_key,
        session=session,
        user=user,
        backend=backend,
    )


@router.post(
    "/sada/transcriptions",
    response_model=APIEnvelope[JobAccepted],
    status_code=status.HTTP_202_ACCEPTED,
)
async def sada_transcription(
    payload: SadaRequest,
    idempotency_key: IdempotencyKey,
    session: DbSession,
    user: RateLimitedUser,
    backend: Backend,
) -> APIEnvelope[JobAccepted]:
    return await _create(
        task_type=TaskType.SADA_TRANSCRIBE,
        payload=payload,
        idempotency_key=idempotency_key,
        session=session,
        user=user,
        backend=backend,
    )
