from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field

from app.models.enums import FeedbackIssue
from app.schemas.common import StrictModel


class FeedbackCreate(StrictModel):
    output_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    is_helpful: bool
    issue_types: list[FeedbackIssue] = Field(default_factory=list, max_length=10)
    comment: str | None = Field(default=None, max_length=3000)
    corrected_output: dict[str, Any] | None = None
    consent_for_training: bool = False
    implicit_signals: dict[str, Any] = Field(default_factory=dict)


class FeedbackView(StrictModel):
    feedback_id: uuid.UUID
    candidate_created: bool
