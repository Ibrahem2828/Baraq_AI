from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class APIError(StrictModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class APIEnvelope(StrictModel, Generic[T]):
    success: bool = True
    data: T | None = None
    error: APIError | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Citation(StrictModel):
    source_id: str
    chunk_id: uuid.UUID | None = None
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = None
    excerpt: str = Field(min_length=1, max_length=1200)
    relevance_score: float | None = Field(default=None, ge=0, le=1)


class HealthStatus(StrictModel):
    service: str
    version: str
    status: str
    database: str | None = None
    redis: str | None = None
    timestamp: datetime
