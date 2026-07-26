from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Annotated, Literal, Union

from pydantic import Field

from app.models.enums import Character, JobStatus, TaskType
from app.schemas.common import Citation, StrictModel
from app.schemas.fahes import FahesRequest
from app.schemas.kholasa import KholasaRequest
from app.schemas.khota import KhotaRequest
from app.schemas.rasheed import RasheedRequest
from app.schemas.sada import SadaRequest

TaskPayload = Annotated[
    Union[FahesRequest, KhotaRequest, RasheedRequest, KholasaRequest, SadaRequest],
    Field(discriminator=None),
]


class JobCreateRequest(StrictModel):
    task_type: TaskType
    payload: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=128)
    backend_request_id: str | None = Field(default=None, max_length=128)
    model_tier: Literal["fast", "balanced", "high_quality"] | None = None
    force_refresh: bool = False


class JobAccepted(StrictModel):
    job_id: uuid.UUID
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
    materialized_resource_type: str | None = None
    materialized_resource_id: str | None = None


class JobView(StrictModel):
    job_id: uuid.UUID
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
