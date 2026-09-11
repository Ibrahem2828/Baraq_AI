"""In-process durable job execution for Lab mode only."""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.standalone import BaraqAIApplication
from app.core.errors import AppError
from app.lab.storage import LabStorage
from app.models.enums import TaskType


class LocalJobManager:
    """Runs a small number of Lab jobs without Redis, Celery, or a worker."""

    def __init__(self, *, storage: LabStorage, application: BaraqAIApplication) -> None:
        self.storage = storage
        self.application = application
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit(
        self,
        *,
        job_id: str,
        workspace_id: str,
        task_type: TaskType,
        input: dict[str, Any],
        thinking: bool | None,
    ) -> None:
        if job_id in self._tasks:
            return
        task = asyncio.create_task(
            self._run(
                job_id=job_id,
                workspace_id=workspace_id,
                task_type=task_type,
                input=input,
                thinking=thinking,
            ),
            name=f"baraq-lab-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    async def _run(
        self,
        *,
        job_id: str,
        workspace_id: str,
        task_type: TaskType,
        input: dict[str, Any],
        thinking: bool | None,
    ) -> None:
        try:
            self.storage.update_job(
                job_id=job_id,
                status="preparing",
                progress_percent=15,
                progress_message="Preparing local source scope",
            )
            self.storage.update_job(
                job_id=job_id,
                status="planning" if task_type == TaskType.KHOTA_GENERATE_PLAN else "generating",
                progress_percent=55,
                progress_message="Running local engine"
                if task_type == TaskType.KHOTA_GENERATE_PLAN
                else "Calling AI provider",
            )
            output = await self.application.run_task(
                workspace_id=workspace_id,
                task_type=task_type,
                input=input,
                thinking=thinking,
            )
            self.storage.update_job(
                job_id=job_id,
                status="completed",
                result={
                    "result": output.result,
                    "evidence": output.evidence,
                    "validation": output.validation,
                    "provider": output.provider,
                },
                progress_percent=100,
                progress_message="Completed",
            )
        except AppError as exc:
            self.storage.update_job(
                job_id=job_id,
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
                progress_percent=100,
                progress_message="Failed safely",
            )
        except Exception:
            self.storage.update_job(
                job_id=job_id,
                status="failed",
                error_code="lab_internal_error",
                error_message="The Lab job failed unexpectedly; no result was accepted.",
                progress_percent=100,
                progress_message="Failed safely",
            )
