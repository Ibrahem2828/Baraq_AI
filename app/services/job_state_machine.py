from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.errors import ConflictError
from app.models.enums import JobStatus

TERMINAL_JOB_STATES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED})

_ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.PREPARING, JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.PREPARING: frozenset(
        {
            JobStatus.RETRIEVING,
            JobStatus.PLANNING,
            JobStatus.GENERATING,
            JobStatus.CANCELED,
            JobStatus.FAILED,
        }
    ),
    JobStatus.RETRIEVING: frozenset(
        {
            JobStatus.PLANNING,
            JobStatus.GENERATING,
            JobStatus.VALIDATING,
            JobStatus.CANCELED,
            JobStatus.FAILED,
        }
    ),
    JobStatus.PLANNING: frozenset({JobStatus.GENERATING, JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.GENERATING: frozenset(
        {JobStatus.VALIDATING, JobStatus.REPAIRING, JobStatus.CANCELED, JobStatus.FAILED}
    ),
    JobStatus.VALIDATING: frozenset(
        {
            JobStatus.REPAIRING,
            JobStatus.MATERIALIZING,
            JobStatus.COMPLETED,
            JobStatus.CANCELED,
            JobStatus.FAILED,
        }
    ),
    JobStatus.REPAIRING: frozenset({JobStatus.VALIDATING, JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.MATERIALIZING: frozenset({JobStatus.COMPLETED, JobStatus.CANCELED, JobStatus.FAILED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class JobTransition:
    from_status: JobStatus
    to_status: JobStatus


class StatefulJob(Protocol):
    status: JobStatus
    progress_message: str
    progress_percent: int


class JobStateMachine:
    """The sole policy for persisted AI job transitions."""

    @staticmethod
    def can_transition(current: JobStatus, target: JobStatus) -> bool:
        return target in _ALLOWED_TRANSITIONS[current]

    @classmethod
    def transition(cls, job: StatefulJob, *, target: JobStatus, message: str) -> JobTransition:
        current = job.status
        if current == target:
            return JobTransition(from_status=current, to_status=target)
        if not cls.can_transition(current, target):
            raise ConflictError(
                f"Job cannot transition from {current.value} to {target.value}",
                code="invalid_job_transition",
            )
        job.status = target
        job.progress_message = message
        # Progress percentages had historically been synthetic.  Keep the
        # compatibility column neutral; clients should render status/stage.
        job.progress_percent = 0
        return JobTransition(from_status=current, to_status=target)
