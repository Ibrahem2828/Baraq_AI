from __future__ import annotations

import uuid
from unittest.mock import patch

from app.models.ai_job import JobDispatchOutboxEvent
from app.services.webhook_delivery import RESULT_WEBHOOK_EVENT
from app.workers.tasks import _enqueue_outbox_event


def _event(*, event_type: str, target_queue: str = "ai_interactive") -> JobDispatchOutboxEvent:
    event = JobDispatchOutboxEvent(
        job_id=uuid.uuid4(),
        event_type=event_type,
        request_id="00000000-0000-0000-0000-000000000001",
        target_queue=target_queue,
    )
    event.id = uuid.uuid4()
    return event


def test_process_ai_job_event_is_sent_to_its_own_recorded_queue() -> None:
    event = _event(event_type="process_ai_job", target_queue="ai_audio")
    with patch("app.workers.tasks.celery_app.send_task") as send_task:
        _enqueue_outbox_event(event)
    send_task.assert_called_once_with(
        "app.workers.tasks.process_ai_job", args=[str(event.job_id)], queue="ai_audio"
    )


def test_process_ai_job_event_defaults_route_to_interactive() -> None:
    event = _event(event_type="process_ai_job")
    with patch("app.workers.tasks.celery_app.send_task") as send_task:
        _enqueue_outbox_event(event)
    send_task.assert_called_once_with(
        "app.workers.tasks.process_ai_job", args=[str(event.job_id)], queue="ai_interactive"
    )


def test_webhook_delivery_event_ignores_target_queue() -> None:
    """Webhook delivery always routes via its own static task_routes entry
    (ai_background) -- target_queue only matters for process_ai_job."""
    event = _event(event_type=RESULT_WEBHOOK_EVENT, target_queue="ai_audio")
    with patch("app.workers.tasks.celery_app.send_task") as send_task:
        _enqueue_outbox_event(event)
    send_task.assert_called_once_with(
        "app.workers.tasks.deliver_result_webhook", args=[str(event.id)]
    )
