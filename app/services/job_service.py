from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.idempotency import build_idempotency_scope, stable_hash
from app.models.ai_job import AIJob, AIOutput
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.backend import CreditReservationRequest
from app.schemas.fahes import FahesRequest
from app.schemas.feedback import FeedbackCreate
from app.schemas.jobs import JobCreateRequest
from app.schemas.kholasa import KholasaRequest
from app.schemas.khota import KhotaRequest
from app.schemas.rasheed import RasheedRequest
from app.schemas.sada import SadaRequest
from app.services.backend_client import BackendClient

TASK_CHARACTER = {
    TaskType.FAHES_GENERATE_QUIZ: Character.FAHES,
    TaskType.KHOTA_GENERATE_PLAN: Character.KHOTA,
    TaskType.RASHEED_RECOMMEND: Character.RASHEED,
    TaskType.KHOLASA_SUMMARIZE: Character.KHOLASA,
    TaskType.SADA_TRANSCRIBE: Character.SADA,
}

TASK_SCHEMAS = {
    TaskType.FAHES_GENERATE_QUIZ: FahesRequest,
    TaskType.KHOTA_GENERATE_PLAN: KhotaRequest,
    TaskType.RASHEED_RECOMMEND: RasheedRequest,
    TaskType.KHOLASA_SUMMARIZE: KholasaRequest,
    TaskType.SADA_TRANSCRIBE: SadaRequest,
}


class JobService:
    def __init__(self, session: AsyncSession, backend: BackendClient) -> None:
        self.session = session
        self.backend = backend

    @staticmethod
    def validate_payload(task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
        schema = TASK_SCHEMAS.get(task_type)
        if schema is None:
            raise ValueError(f"Unsupported task type: {task_type}")
        return schema.model_validate(payload).model_dump(mode="json")

    @staticmethod
    def estimate_units(task_type: TaskType, payload: dict[str, Any]) -> int:
        if task_type == TaskType.FAHES_GENERATE_QUIZ:
            return max(1, int(payload.get("question_count", 10) / 5))
        if task_type == TaskType.KHOTA_GENERATE_PLAN:
            return 2
        if task_type == TaskType.RASHEED_RECOMMEND:
            return 1
        if task_type == TaskType.KHOLASA_SUMMARIZE:
            return max(1, len(payload.get("source_ids", [])))
        if task_type == TaskType.SADA_TRANSCRIBE:
            return 5
        return 1

    async def create_job(self, *, user_id: str, request: JobCreateRequest) -> tuple[AIJob, bool]:
        payload = self.validate_payload(request.task_type, request.payload)
        idempotency_hash = build_idempotency_scope(
            user_id=user_id,
            task_type=request.task_type.value,
            key=request.idempotency_key,
        )
        if request.force_refresh:
            idempotency_hash = stable_hash(
                {"base": idempotency_hash, "nonce": str(uuid.uuid4())}
            )
        else:
            existing = await self.session.scalar(
                select(AIJob)
                .where(AIJob.idempotency_hash == idempotency_hash)
                .options(selectinload(AIJob.output))
            )
            if existing:
                return existing, True

        reservation = await self.backend.reserve_credits(
            CreditReservationRequest(
                user_id=user_id,
                task_type=request.task_type.value,
                idempotency_key=request.idempotency_key,
                estimated_units=self.estimate_units(request.task_type, payload),
                metadata={"backend_request_id": request.backend_request_id},
            )
        )
        if not reservation.approved:
            raise ConflictError(
                "AI usage limit reached",
                code=reservation.reason_code or "ai_credit_limit_reached",
            )
        source_ids = list(payload.get("source_ids") or [])
        if request.task_type == TaskType.SADA_TRANSCRIBE:
            source_ids = [str(payload["source_id"])]
        job = AIJob(
            user_id=user_id,
            backend_request_id=request.backend_request_id,
            task_type=request.task_type,
            character=TASK_CHARACTER[request.task_type],
            status=JobStatus.QUEUED,
            progress_percent=0,
            progress_message="تم قبول الطلب ووضعه في قائمة المعالجة",
            idempotency_hash=idempotency_hash,
            input_hash=stable_hash(payload),
            request_payload=payload,
            source_ids=source_ids,
            model_tier=request.model_tier,
            credit_reservation_id=reservation.reservation_id,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)

        from app.workers.tasks import process_ai_job

        process_ai_job.delay(str(job.id))
        return job, False

    async def get_job(self, *, job_id: uuid.UUID, user_id: str) -> AIJob:
        job = await self.session.scalar(
            select(AIJob)
            .where(AIJob.id == job_id, AIJob.user_id == user_id)
            .options(selectinload(AIJob.output), selectinload(AIJob.attempts))
        )
        if not job:
            raise NotFoundError("AI job not found")
        return job

    async def list_jobs(self, *, user_id: str, limit: int, offset: int) -> list[AIJob]:
        statement = (
            select(AIJob)
            .where(AIJob.user_id == user_id)
            .order_by(AIJob.created_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(AIJob.output))
        )
        return list((await self.session.scalars(statement)).all())

    async def cancel_job(self, *, job_id: uuid.UUID, user_id: str) -> AIJob:
        job = await self.get_job(job_id=job_id, user_id=user_id)
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}:
            return job
        job.status = JobStatus.CANCELED
        job.progress_message = "تم إلغاء الطلب"
        job.completed_at = datetime.now(UTC)
        await self.session.commit()
        if job.credit_reservation_id:
            await self.backend.refund_credits(
                reservation_id=job.credit_reservation_id,
                reason="user_canceled",
            )
        return job
