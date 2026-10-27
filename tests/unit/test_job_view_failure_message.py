from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.api.v1.jobs import to_job_view
from app.models.enums import Character, JobStatus, TaskType
from app.services.webhook_delivery import public_failure_message


def failed_job(**overrides: Any) -> Any:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "backend_request_id": "client-job-1",
        "request_id": str(uuid.uuid4()),
        "task_type": TaskType.FAHES_GENERATE_QUIZ,
        "character": Character.FAHES,
        "status": JobStatus.FAILED,
        "progress_percent": 40,
        "progress_message": "Job processing failed",
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "completed_at": now,
        "error_code": "provider_timeout",
        "error_message": (
            "ReadTimeout: POST https://api.openai.com/v1/responses "
            "key=sk-secret body=<student essay>"
        ),
        "output": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_the_status_view_never_returns_the_raw_diagnostic() -> None:
    view = to_job_view(failed_job())

    assert view.error_code == "provider_timeout"
    assert view.error_message == public_failure_message("provider_timeout")
    assert "sk-secret" not in (view.error_message or "")
    assert "student essay" not in (view.error_message or "")


def test_an_unreviewed_error_code_gets_the_generic_message() -> None:
    view = to_job_view(failed_job(error_code="ValidationError"))
    assert view.error_message == "The AI request could not be completed."


def test_a_job_without_an_error_has_no_message() -> None:
    view = to_job_view(failed_job(status=JobStatus.QUEUED, error_code=None, error_message=None))
    assert view.error_message is None
