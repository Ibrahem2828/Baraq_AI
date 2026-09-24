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


# Only stable, actionable text crosses the service boundary. The original
# exception remains in the private AI database/logs for operators, while
# Django and the browser receive a message that cannot contain provider
# payloads, source text, internal URLs, or credentials.
_PUBLIC_FAILURE_MESSAGES = {
    "pdf_ocr_required": "This PDF does not contain extractable text and requires OCR.",
    "unsupported_source_format": "This source format is not supported.",
    "source_version_changed": "The source changed after this job was created.",
    "source_checksum_mismatch": "The downloaded source did not match the requested version.",
    "source_download_failed": "The source could not be downloaded.",
    "source_project_mismatch": "The selected source is not part of this project.",
    "source_size_mismatch": "The downloaded source size did not match its manifest.",
    "source_too_large": "The source exceeds the AI processing limit.",
    "source_ingestion_failed": "The source could not be prepared for AI use.",
    "text_decode_failed": "The text source could not be decoded.",
    "pdf_read_failed": "The PDF could not be read.",
    "docx_read_failed": "The DOCX document could not be read.",
    "pptx_read_failed": "The PPTX presentation could not be read.",
    "empty_source": "The source does not contain readable text.",
    "empty_chunks": "The source does not contain enough usable text.",
    "embedding_failed": "The source could not be indexed.",
    "embedding_count_mismatch": "The source index could not be verified.",
    "retrieval_failed": "Relevant source material could not be retrieved.",
    "insufficient_source_context": "The selected sources do not contain enough relevant material.",
    "missing_authoritative_data": "There is not enough learner performance data for this analysis.",
    "khota_no_study_days": "No available study days remain in the selected period.",
    "khota_constraint_violation": "The requested study-plan constraints cannot be satisfied.",
    "audio_source_required": "Sada requires a supported audio source.",
    "audio_too_large": "The audio source exceeds the transcription limit.",
    "empty_transcription": "No speech could be transcribed from this audio source.",
    "transcription_failed": "The audio could not be transcribed.",
    "provider_timeout": "The AI provider timed out.",
    "provider_rate_limited": "The AI provider is temporarily rate limited.",
    "provider_unavailable": "The AI provider is temporarily unavailable.",
    "all_providers_failed": "The AI provider is temporarily unavailable.",
    "no_provider_available": "No AI provider is currently available for this request.",
    "result_validation_failed": "The generated result did not pass validation.",
    "output_validation_failed": "The generated result did not pass validation.",
    "worker_interrupted_execution_uncertain": "The job stopped safely after a worker interruption.",
}

_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "source_download_failed",
        "source_ingestion_failed",
        "embedding_failed",
        "retrieval_failed",
        "transcription_failed",
        "provider_timeout",
        "provider_rate_limited",
        "provider_unavailable",
        "all_providers_failed",
    }
)


def public_failure_message(code: str | None) -> str:
    return _PUBLIC_FAILURE_MESSAGES.get(
        str(code or ""), "The AI request could not be completed."
    )


def build_terminal_webhook_payload(
    *, event: JobDispatchOutboxEvent, job: AIJob
) -> dict[str, Any]:
    """Build the callback for any terminal job without leaking diagnostics."""
    if job.status == JobStatus.COMPLETED:
        if job.output is None:
            raise ValueError("Completed result webhook requires a completed job output")
        return build_result_webhook_payload(event=event, job=job, output=job.output)
    if job.status == JobStatus.FAILED:
        code = str(job.error_code or "provider_unavailable")[:100]
        return {
            "event_id": str(event.id),
            "event_type": "ai.job.failed",
            "job_id": job.backend_request_id or str(job.id),
            "status": JobStatus.FAILED.value,
            "error_code": code,
            "error_message": public_failure_message(code),
            "retryable": code in _RETRYABLE_FAILURE_CODES,
        }
    if job.status == JobStatus.CANCELED:
        return {
            "event_id": str(event.id),
            "event_type": "ai.job.canceled",
            "job_id": job.backend_request_id or str(job.id),
            "status": JobStatus.CANCELED.value,
        }
    raise ValueError("Result webhook requires a terminal job")


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
            payload = build_terminal_webhook_payload(event=event, job=job)
        await backend.deliver_job_webhook(payload=payload)
        await dispatcher.mark_dispatched(uuid.UUID(event_id))
        logger.info(
            "ai_result_webhook_delivered",
            event_id=event_id,
            # Only the completed payload carries metadata; failed/canceled
            # ones do not, and reading it there raised KeyError right after
            # every such delivery had already succeeded.
            request_id=str(job.request_id),
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
