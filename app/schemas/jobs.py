from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.core.task_types import normalize_task_type
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.common import Citation, StrictModel


class JobCreateRequest(StrictModel):
    """Internal normalized request; it is constructed only by the Django route."""

    task_type: TaskType
    payload: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=128)
    backend_request_id: str = Field(min_length=1, max_length=128)
    model_tier: Literal["fast", "balanced", "high_quality"] | None = None

    @field_validator("task_type", mode="before")
    @classmethod
    def normalize_legacy_task_type(cls, value: object) -> str:
        return normalize_task_type(value)


class DjangoJobCreateRequest(StrictModel):
    """Django -> AI job submission contract for POST /api/ai/v1/jobs."""

    client_job_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=64)
    task_type: TaskType
    input: dict[str, Any]
    model_tier: Literal["fast", "balanced", "high_quality"] | None = None

    @field_validator("task_type", mode="before")
    @classmethod
    def normalize_legacy_task_type(cls, value: object) -> str:
        return normalize_task_type(value)


class JobAccepted(StrictModel):
    job_id: str
    ai_job_id: uuid.UUID
    status: JobStatus
    task_type: TaskType
    character: Character
    status_url: str
    estimated_wait_seconds: int | None = None
    cache_hit: bool = False


class JobOutputView(StrictModel):
    output_id: uuid.UUID
    result: dict[str, Any]
    citations: list[Citation] = Field(default_factory=list)
    validation_status: str
    quality_score: float | None = None
    groundedness_score: float | None = None
    model_name: str
    provider_account: str


class JobView(StrictModel):
    job_id: str
    ai_job_id: uuid.UUID
    task_type: TaskType
    character: Character
    status: JobStatus
    progress_percent: int = Field(ge=0, le=100)
    progress_message: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    output: JobOutputView | None = None
