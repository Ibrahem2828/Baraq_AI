from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.core.idempotency import build_idempotency_scope, stable_hash
from app.models.ai_job import AIJob
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.fahes import FahesRequest
from app.schemas.jobs import JobCreateRequest
from app.schemas.kholasa import KholasaRequest
from app.schemas.khota import KhotaRequest
from app.schemas.rasheed import RasheedRequest
from app.schemas.sada import SadaRequest

TASK_CHARACTER = {
    TaskType.FAHES_GENERATE_QUIZ: Character.FAHES,
    TaskType.KHOTA_GENERATE_PLAN: Character.KHOTA,
    TaskType.RASHEED_RECOMMENDATIONS: Character.RASHEED,
    TaskType.KHOLASA_GENERATE_SUMMARY: Character.KHOLASA,
    TaskType.SADA_TRANSCRIBE_AUDIO: Character.SADA,
}

TASK_SCHEMAS = {
    TaskType.FAHES_GENERATE_QUIZ: FahesRequest,
    TaskType.KHOTA_GENERATE_PLAN: KhotaRequest,
    TaskType.RASHEED_RECOMMENDATIONS: RasheedRequest,
    TaskType.KHOLASA_GENERATE_SUMMARY: KholasaRequest,
    TaskType.SADA_TRANSCRIBE_AUDIO: SadaRequest,
}


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def validate_payload(task_type: TaskType, payload: dict[str, Any]) -> dict[str, Any]:
        schema = TASK_SCHEMAS.get(task_type)
        if schema is None:
            raise ValueError(f"Unsupported task type: {task_type}")
        return schema.model_validate(payload).model_dump(mode="json")

    async def create_job(self, *, user_id: str, request: JobCreateRequest) -> tuple[AIJob, bool]:
        payload = self.validate_payload(request.task_type, request.payload)
        idempotency_hash = build_idempotency_scope(
            user_id=user_id,
            task_type=request.task_type.value,
            key=request.idempotency_key,
        )
        existing = await self.session.scalar(
            select(AIJob)
            .where(AIJob.idempotency_hash == idempotency_hash)
            .options(selectinload(AIJob.output))
        )
        if existing:
            return existing, True

        source_ids = list(payload.get("source_ids") or [])
        if request.task_type == TaskType.SADA_TRANSCRIBE_AUDIO:
            source_ids = [str(payload["source_id"])]
        job = AIJob(
            user_id=user_id,
            backend_request_id=request.backend_request_id,
            task_type=request.task_type,
            character=TASK_CHARACTER[request.task_type],
            status=JobStatus.QUEUED,
            progress_percent=0,
            progress_message="Job accepted and queued for processing",
            idempotency_hash=idempotency_hash,
            input_hash=stable_hash(payload),
            request_payload=payload,
            source_ids=source_ids,
            model_tier=request.model_tier,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)

        from app.workers.tasks import process_ai_job

        process_ai_job.delay(str(job.id))
        return job, False

    async def get_job_by_client_id(self, *, client_job_id: str) -> AIJob:
        job = await self.session.scalar(
            select(AIJob)
            .where(AIJob.backend_request_id == client_job_id)
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

    async def cancel_job_by_client_id(self, *, client_job_id: str) -> AIJob:
        job = await self.get_job_by_client_id(client_job_id=client_job_id)
        if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}:
            return job
        # Phase 4 replaces this direct transition with the persisted state machine.
        job.status = JobStatus.CANCELED
        job.progress_message = "Job canceled by Django gateway"
        job.completed_at = datetime.now(UTC)
        await self.session.commit()
        return job
