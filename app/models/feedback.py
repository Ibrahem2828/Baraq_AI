from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CandidateStatus, FeedbackIssue


class AIFeedback(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_feedback"
    __table_args__ = (
        UniqueConstraint("output_id", "user_id", name="uq_feedback_output_user"),
    )

    output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_outputs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    is_helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issue_types: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    corrected_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    consent_for_training: Mapped[bool] = mapped_column(Boolean, default=False)
    implicit_signals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class TrainingDatasetCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_training_candidates"

    output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_outputs.id", ondelete="CASCADE"), index=True
    )
    feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_feedback.id", ondelete="SET NULL"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    anonymized: Mapped[bool] = mapped_column(Boolean, default=False)
    pii_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus, name="ai_candidate_status"), default=CandidateStatus.PENDING
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))


class DatasetVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_dataset_versions"

    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class EvaluationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_evaluation_runs"

    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_dataset_versions.id", ondelete="SET NULL")
    )
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    provider_account: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    code_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
