from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import ConflictError
from app.models.enums import JobStatus
from app.services.job_state_machine import JobStateMachine


def test_valid_job_transition_updates_status_and_message() -> None:
    job = SimpleNamespace(status=JobStatus.QUEUED, progress_message="", progress_percent=75)
    JobStateMachine.transition(job, target=JobStatus.PREPARING, message="Preparing")
    assert job.status == JobStatus.PREPARING
    assert job.progress_message == "Preparing"
    assert job.progress_percent == 0


def test_terminal_job_cannot_reenter_processing() -> None:
    job = SimpleNamespace(status=JobStatus.COMPLETED, progress_message="", progress_percent=0)
    with pytest.raises(ConflictError) as error:
        JobStateMachine.transition(job, target=JobStatus.GENERATING, message="bad")
    assert error.value.code == "invalid_job_transition"


def test_cancellation_is_a_valid_active_state_transition() -> None:
    job = SimpleNamespace(status=JobStatus.GENERATING, progress_message="", progress_percent=0)
    JobStateMachine.transition(job, target=JobStatus.CANCELED, message="Canceled")
    assert job.status == JobStatus.CANCELED
