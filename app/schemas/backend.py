from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import AnyHttpUrl, Field

from app.schemas.common import StrictModel


class SourceManifest(StrictModel):
    source_id: str
    owner_user_id: str
    title: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(min_length=64, max_length=64)
    download_url: AnyHttpUrl
    download_url_expires_at: str | None = None
    subject_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreditReservationRequest(StrictModel):
    user_id: str
    task_type: str
    idempotency_key: str
    estimated_units: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreditReservationResponse(StrictModel):
    reservation_id: str
    approved: bool
    remaining_units: int | None = None
    reason_code: str | None = None


class CreditCommitRequest(StrictModel):
    reservation_id: str
    actual_units: int = Field(ge=0)
    actual_cost_usd: float = Field(ge=0)
    provider: str
    model: str


class MaterializeRequest(StrictModel):
    user_id: str
    task_type: str
    job_id: str
    output_id: str
    result: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list)


class MaterializeResponse(StrictModel):
    resource_type: str
    resource_id: str
    resource_url: str | None = None


class LearnerContext(StrictModel):
    user_id: str
    education_stage: str | None = None
    grade_level: str | None = None
    specialization: str | None = None
    daily_study_minutes: int | None = None
    selected_subjects: list[dict[str, Any]] = Field(default_factory=list)
    exam_dates: dict[str, date] = Field(default_factory=dict)
    authoritative_metrics: dict[str, Any] = Field(default_factory=dict)
