from __future__ import annotations

import uuid
import warnings
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.core.task_types import normalize_task_type
from app.models.enums import Character, JobStatus, TaskType
from app.schemas.common import Citation, StrictModel


class JobCreateRequest(StrictModel):
    """Canonical internal job request constructed at the Django boundary."""

    client_job_id: str = Field(min_length=1, max_length=128)
    task_type: TaskType
    project_id: str = Field(min_length=1, max_length=64)
    input: dict[str, Any]
    model_policy: ModelPolicy
    trace: TraceContext

    @field_validator("task_type", mode="before")
    @classmethod
    def normalize_legacy_task_type(cls, value: object) -> str:
        return normalize_task_type(value)


class ModelPolicy(StrictModel):
    tier: Literal["fast", "balanced", "high_quality"]
    allow_fallback: bool


class TraceContext(StrictModel):
    request_id: uuid.UUID


class DjangoJobCreateRequestV2(StrictModel):
    """The strict, versioned Django -> AI job contract.

    Wire shape is Baraq_MD_Blueprint/01_BACKEND.md §5.2 and
    02_AI_PLATFORM.md §5.2's documented payload, verbatim: project_id,
    source_ids and source_versions travel at the top level, and the trace
    envelope is keyed "trace_context" on the wire (kept as `.trace`
    internally via the alias below, since that name is used pervasively
    throughout this service).
    """

    contract_version: Literal["2.0"]
    client_job_id: uuid.UUID
    user_id: str = Field(min_length=1, max_length=64)
    project_id: str = Field(min_length=1, max_length=64)
    task_type: TaskType
    source_ids: list[str] = Field(default_factory=list)
    source_versions: dict[str, str] = Field(default_factory=dict)
    input: dict[str, Any]
    model_policy: ModelPolicy
    trace: TraceContext = Field(alias="trace_context")

    @field_validator("task_type", mode="before")
    @classmethod
    def reject_noncanonical_task_type(cls, value: object) -> str:
        raw_value = value.value if isinstance(value, TaskType) else str(value)
        if raw_value != normalize_task_type(raw_value):
            raise ValueError("legacy task types are not valid in contract 2.0")
        return raw_value

    def to_internal(self) -> JobCreateRequest:
        return JobCreateRequest(
            client_job_id=str(self.client_job_id),
            task_type=self.task_type,
            project_id=self.project_id,
            input=self.input,
            model_policy=self.model_policy,
            trace=self.trace,
        )


class DjangoJobCreateRequest(StrictModel):
    """Deprecated V1 compatibility adapter; V2 is the only primary contract."""

    client_job_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=64)
    task_type: TaskType
    input: dict[str, Any]
    model_tier: Literal["fast", "balanced", "high_quality"] | None = None

    @field_validator("task_type", mode="before")
    @classmethod
    def normalize_legacy_task_type(cls, value: object) -> str:
        return normalize_task_type(value)

    def to_internal(self, *, idempotency_key: str | None) -> JobCreateRequest:
        warnings.warn(
            "The unversioned Django job contract is deprecated; migrate to contract_version=2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        # V1 did not require a UUID.  Preserve the existing client key so old
        # retries remain idempotent until Django rolls out V2.
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, f"baraq-v1:{self.client_job_id}")
        return JobCreateRequest(
            client_job_id=self.client_job_id,
            task_type=self.task_type,
            project_id=None,
            input=self.input,
            model_policy=ModelPolicy(tier=self.model_tier or "balanced", allow_fallback=True),
            trace=TraceContext(request_id=request_id),
        )


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
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    security_flags: list[dict[str, Any]] = Field(default_factory=list)
    validation_report: dict[str, Any] = Field(default_factory=dict)


class JobView(StrictModel):
    job_id: str
    ai_job_id: uuid.UUID
    request_id: uuid.UUID
    task_type: TaskType
    character: Character
    status: JobStatus
    stage: JobStatus
    progress_percent: int = Field(ge=0, le=100)
    progress_message: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    output: JobOutputView | None = None
