from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from app.schemas.common import StrictModel


class SourceManifest(StrictModel):
    source_id: str
    owner_user_id: str
    title: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(min_length=64, max_length=64)
    download_url: str | None = None
    download_url_expires_at: datetime | None = None
    subject_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CollectionManifest(StrictModel):
    collection_id: str
    owner_user_id: str
    title: str
    source_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LearnerContext(StrictModel):
    user_id: str
    education_stage: str | None = None
    grade_level: str | None = None
    specialization: str | None = None
    daily_study_minutes: int | None = None
    selected_subjects: list[dict[str, Any]] = Field(default_factory=list)
    exam_dates: dict[str, date] = Field(default_factory=dict)
    authoritative_metrics: dict[str, Any] = Field(default_factory=dict)
