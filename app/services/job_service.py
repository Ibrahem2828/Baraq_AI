from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.idempotency import build_idempotency_scope, stable_hash
from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.fahes import FahesRequest
from app.schemas.jobs import JobCreateRequest
from app.schemas.kholasa import KholasaRequest
from app.schemas.khota import KhotaRequest
from app.schemas.rasheed import RasheedRequest
from app.schemas.sada import SadaRequest
from app.services.backend_client import BackendClient
from app.services.job_state_machine import TERMINAL_JOB_STATES, JobStateMachine

TASK_CHARACTER = {
    TaskType.FAHES_GENERATE_QUIZ: Character.FAHES,
    TaskType.KHOTA_GENERATE_PLAN: Character.KHOTA,
    TaskType.RASHEED_RECOMMENDATIONS: Character.RASHEED,
    TaskType.KHOLASA_GENERATE_SUMMARY: Character.KHOLASA,
    TaskType.SADA_TRANSCRIBE_AUDIO: Character.SADA,
}

TASK_SCHEMAS: dict[TaskType, type[BaseModel]] = {
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
            raise ValidationFailure("Task type is not supported", code="invalid_task_type")
        try:
            return schema.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            # Validation errors are intentionally reduced to stable structural
            # information so source content never leaks through the API.
            raise ValidationFailure(
                "Task input does not match the required schema",
                code="invalid_task_input",
                details={
                    "errors": [
                        {"loc": list(error["loc"]), "type": error["type"]}
                        for error in exc.errors(include_input=False)
                    ]
                },
            ) from exc

    @staticmethod
    async def freeze_source_versions(
        *,
        user_id: str,
        project_id: str | None,
        task_type: TaskType,
        payload: dict[str, Any],
        backend: BackendClient,
    ) -> dict[str, str]:
        """Freeze the exact Django manifest versions authorised for this job.

        Source ownership is checked by Django before a durable job is written;
        the worker later rejects a changed manifest rather than silently using
        newer material with an old request. Also the first point a source's
        project is checked against the job's own project_id (blueprint
        02_AI_PLATFORM.md §3.2) -- before any durable job row is even created.
        """
        source_ids = [str(item) for item in payload.get("source_ids") or []]
        if task_type == TaskType.SADA_TRANSCRIBE_AUDIO:
            source_ids = [str(payload["source_id"])]
        if source_ids and project_id is None:
            # Blueprint 02_AI_PLATFORM.md §3.2: "every task that depends on
            # sources carries a project_id" -- user_id + source_ids alone is
            # not a sufficient authorization scope.
            raise ValidationFailure(
                "A project_id is required for a request that uses sources",
                code="project_id_required",
            )
        versions: dict[str, str] = {}
        for source_id in sorted(set(source_ids)):
            manifest = await backend.get_source_manifest(
                source_id=source_id, user_id=user_id, project_id=project_id
            )
            versions[source_id] = manifest.content_sha256
        return versions

    async def create_job(
        self, *, user_id: str, request: JobCreateRequest, source_versions: dict[str, str]
    ) -> tuple[AIJob, bool]:
        payload = self.validate_payload(request.task_type, request.input)
        idempotency_hash = build_idempotency_scope(
            user_id=user_id,
            client_job_id=request.client_job_id,
        )
        input_hash = stable_hash(
            {
                "task_type": request.task_type.value,
                "input": payload,
                "model_policy": request.model_policy.model_dump(mode="json"),
            }
        )
        try:
            async with self.session.begin():
                existing = await self.session.scalar(
                    select(AIJob)
                    .where(AIJob.idempotency_hash == idempotency_hash)
                    .options(selectinload(AIJob.output))
                    .with_for_update()
                )
                if existing:
                    return self._existing_idempotency_result(existing, input_hash)

                source_ids = list(payload.get("source_ids") or [])
                if request.task_type == TaskType.SADA_TRANSCRIBE_AUDIO:
                    source_ids = [str(payload["source_id"])]
                job = AIJob(
                    user_id=user_id,
                    project_id=request.project_id,
                    backend_request_id=request.client_job_id,
                    request_id=str(request.trace.request_id),
                    task_type=request.task_type,
                    character=TASK_CHARACTER[request.task_type],
                    status=JobStatus.QUEUED,
                    progress_percent=0,
                    progress_message="Job accepted and queued for durable dispatch",
                    idempotency_hash=idempotency_hash,
                    input_hash=input_hash,
                    request_payload=payload,
                    source_ids=source_ids,
                    source_versions=source_versions,
                    model_tier=request.model_policy.tier,
                    allow_fallback=request.model_policy.allow_fallback,
                )
                self.session.add(job)
                await self.session.flush()
                self.session.add(
                    JobDispatchOutboxEvent(
                        job_id=job.id,
                        event_type="process_ai_job",
                        request_id=str(request.trace.request_id),
                    )
                )
            await self.session.refresh(job)
            # Celery delivery is deliberately not initiated here.  The outbox
            # dispatcher can safely retry even if this API process dies now.
            return job, False
        except IntegrityError:
            # A concurrent insert won the database constraint.  Re-read and
            # apply the exact same semantic idempotency decision.
            await self.session.rollback()
            existing = await self.session.scalar(
                select(AIJob)
                .where(AIJob.idempotency_hash == idempotency_hash)
                .options(selectinload(AIJob.output))
            )
            if existing is None:
                raise
            return self._existing_idempotency_result(existing, input_hash)

    async def existing_idempotency_result(
        self, *, user_id: str, request: JobCreateRequest
    ) -> tuple[AIJob, bool] | None:
        """Return a prior semantic request before contacting source services.

        A completed retry must remain idempotent even if its source has since
        been deleted or the Django source endpoint is temporarily unavailable.
        The insert transaction still re-checks this condition for races.
        """
        payload = self.validate_payload(request.task_type, request.input)
        input_hash = stable_hash(
            {
                "task_type": request.task_type.value,
                "input": payload,
                "model_policy": request.model_policy.model_dump(mode="json"),
            }
        )
        idempotency_hash = build_idempotency_scope(
            user_id=user_id,
            client_job_id=request.client_job_id,
        )
        existing = await self.session.scalar(
            select(AIJob)
            .where(AIJob.idempotency_hash == idempotency_hash)
            .options(selectinload(AIJob.output))
        )
        return self._existing_idempotency_result(existing, input_hash) if existing else None

    @staticmethod
    def _existing_idempotency_result(existing: AIJob, input_hash: str) -> tuple[AIJob, bool]:
        if existing.input_hash != input_hash:
            raise ConflictError(
                "client_job_id was already used with a different request",
                code="idempotency_conflict",
            )
        return existing, True

    async def get_job_by_client_id(self, *, client_job_id: str, user_id: str) -> AIJob:
        # backend_request_id is only unique per (user_id, backend_request_id) --
        # see uq_ai_jobs_user_backend_request -- so user_id must always be part
        # of this lookup or two tenants' jobs could collide on the same id.
        job = await self.session.scalar(
            select(AIJob)
            .where(AIJob.backend_request_id == client_job_id, AIJob.user_id == user_id)
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

    async def cancel_job_by_client_id(self, *, client_job_id: str, user_id: str) -> AIJob:
        async with self.session.begin():
            job = await self.session.scalar(
                select(AIJob)
                .where(AIJob.backend_request_id == client_job_id, AIJob.user_id == user_id)
                .options(selectinload(AIJob.output))
                .with_for_update()
            )
            if not job:
                raise NotFoundError("AI job not found")
            if job.status in TERMINAL_JOB_STATES:
                return job
            JobStateMachine.transition(
                job, target=JobStatus.CANCELED, message="Job canceled by Django gateway"
            )
            job.completed_at = datetime.now(UTC)
        return job
