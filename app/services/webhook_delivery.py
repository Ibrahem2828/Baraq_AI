"""Durable AI-result delivery to Django using the existing HMAC V2 client."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.ai_job import AIJob, AIOutput, JobDispatchOutboxEvent
from app.models.enums import DispatchOutboxStatus, JobStatus
from app.services.backend_client import BackendClient
from app.services.outbox_dispatcher import OutboxDispatcher

RESULT_WEBHOOK_EVENT = "deliver_result_webhook"
logger = get_logger(__name__)


def build_result_webhook_payload(
    *, event: JobDispatchOutboxEvent, job: AIJob, output: AIOutput
) -> dict[str, Any]:
    """Return the stable payload Django keys idempotency on ``event_id``."""
    return {
        "event_id": str(event.id),
        "event_type": "ai.job.completed",
        "job_id": job.backend_request_id or str(job.id),
        "status": JobStatus.COMPLETED.value,
        "result": output.result_json,
        "metadata": {
            "request_id": job.request_id,
            "verification": output.validation_report,
            "quality": {
                "quality_score": output.quality_score,
                "groundedness_score": output.groundedness_score,
            },
            "usage": {
                "input_tokens": output.input_tokens,
                "output_tokens": output.output_tokens,
                "total_tokens": output.total_tokens,
                "estimated_cost_usd": output.estimated_cost_usd,
            },
            "provider": {
                "account": output.provider_account.value,
                "model": output.model_name,
                "response_id": output.provider_response_id,
            },
            "prompt": {
                "name": job.prompt_name,
                "version": job.prompt_version,
                "checksum": job.prompt_checksum,
            },
            "warnings": output.warnings,
            "security_flags": output.security_flags,
        },
    }


async def deliver_result_webhook(event_id: str) -> None:
    """Send one stored result; failures requeue the *webhook*, never generation."""
    dispatcher = OutboxDispatcher()
    backend = BackendClient()
    try:
        async with AsyncSessionLocal() as session:
            event = await session.scalar(
                select(JobDispatchOutboxEvent)
                .where(JobDispatchOutboxEvent.id == uuid.UUID(event_id))
                .options(selectinload(JobDispatchOutboxEvent.job).selectinload(AIJob.output))
            )
            if event is None or event.status == DispatchOutboxStatus.DISPATCHED:
                return
            if event.event_type != RESULT_WEBHOOK_EVENT:
                raise ValueError(f"Unsupported webhook outbox event: {event.event_type}")
            job = event.job
            output = job.output
            if job.status != JobStatus.COMPLETED or output is None:
                raise ValueError("Completed result webhook requires a completed job output")
            payload = build_result_webhook_payload(event=event, job=job, output=output)
        await backend.deliver_job_webhook(payload=payload)
        await dispatcher.mark_dispatched(uuid.UUID(event_id))
        logger.info(
            "ai_result_webhook_delivered",
            event_id=event_id,
            request_id=payload["metadata"]["request_id"],
        )
    except AppError as exc:
        await dispatcher.release_for_retry(uuid.UUID(event_id), exc)
        logger.warning(
            "ai_result_webhook_retry_scheduled",
            event_id=event_id,
            error_code=exc.code,
        )
        raise
    finally:
        await backend.aclose()
