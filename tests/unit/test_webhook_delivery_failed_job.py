"""Delivering the webhook of a failed job must not raise afterwards."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest

import app.services.webhook_delivery as webhook_delivery
from app.models.ai_job import AIJob, JobDispatchOutboxEvent
from app.models.enums import Character, DispatchOutboxStatus, JobStatus, TaskType
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT, deliver_result_webhook


def _failed_job_event() -> JobDispatchOutboxEvent:
    job = AIJob(
        id=uuid.uuid4(),
        user_id="user-1",
        project_id="project-1",
        request_id=str(uuid.uuid4()),
        task_type=TaskType.SADA_TRANSCRIBE_AUDIO,
        character=Character.SADA,
        status=JobStatus.FAILED,
        error_code="transcription_failed",
        idempotency_hash="a" * 64,
        input_hash="b" * 64,
    )
    event = JobDispatchOutboxEvent(
        id=uuid.uuid4(),
        job_id=job.id,
        request_id=job.request_id,
        event_type=RESULT_WEBHOOK_EVENT,
        status=DispatchOutboxStatus.PENDING,
    )
    event.job = job
    return event


class _Session:
    def __init__(self, event: JobDispatchOutboxEvent) -> None:
        self.event = event

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def scalar(self, statement: Any) -> JobDispatchOutboxEvent:
        return self.event


class _Backend:
    delivered: ClassVar[list[dict[str, Any]]] = []

    async def deliver_job_webhook(self, *, payload: dict[str, Any]) -> None:
        self.delivered.append(payload)

    async def aclose(self) -> None:
        return None


class _Dispatcher:
    dispatched: ClassVar[list[uuid.UUID]] = []

    async def mark_dispatched(self, event_id: uuid.UUID) -> None:
        self.dispatched.append(event_id)


@pytest.mark.asyncio
async def test_a_failed_job_webhook_is_delivered_without_a_trailing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production 2026-09-24: the success log read payload["metadata"], which
    only completed payloads have, so every failed/canceled delivery raised
    KeyError after it had already been sent and marked dispatched."""
    event = _failed_job_event()
    _Backend.delivered.clear()
    _Dispatcher.dispatched.clear()
    monkeypatch.setattr(webhook_delivery, "AsyncSessionLocal", lambda: _Session(event))
    monkeypatch.setattr(webhook_delivery, "BackendClient", _Backend)
    monkeypatch.setattr(webhook_delivery, "OutboxDispatcher", _Dispatcher)

    await deliver_result_webhook(str(event.id))

    assert [payload["event_type"] for payload in _Backend.delivered] == ["ai.job.failed"]
    assert _Dispatcher.dispatched == [event.id]
