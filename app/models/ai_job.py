from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Character, JobStatus, ProviderAccount, ProviderAttemptStatus, TaskType


class AIJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        Index("ix_ai_jobs_user_created", "user_id", "created_at"),
        Index("ix_ai_jobs_task_status", "task_type", "status"),
    )

    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    backend_request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="ai_task_type"), nullable=False)
    character: Mapped[Character] = mapped_column(Enum(Character, name="ai_character"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="ai_job_status"), default=JobStatus.QUEUED, index=True
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str] = mapped_column(String(255), default="تم استلام الطلب")
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    prompt_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    model_tier: Mapped[str | None] = mapped_column(String(32))
    credit_reservation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    output: Mapped["AIOutput | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    attempts: Mapped[list["ProviderAttempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class AIOutput(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_outputs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    validation_status: Mapped[str] = mapped_column(String(32), default="valid")
    quality_score: Mapped[float | None] = mapped_column(Float)
    groundedness_score: Mapped[float | None] = mapped_column(Float)
    provider_account: Mapped[ProviderAccount] = mapped_column(
        Enum(ProviderAccount, name="ai_provider_account")
    )
    provider_response_id: Mapped[str | None] = mapped_column(String(128), index=True)
    model_name: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    provider_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    materialized_resource_type: Mapped[str | None] = mapped_column(String(64))
    materialized_resource_id: Mapped[str | None] = mapped_column(String(128))

    job: Mapped[AIJob] = relationship(back_populates="output")


class ProviderAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_provider_attempts"
    __table_args__ = (Index("ix_provider_attempt_job_order", "job_id", "attempt_number"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_account: Mapped[ProviderAccount] = mapped_column(
        Enum(ProviderAccount, name="ai_provider_attempt_account")
    )
    model_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[ProviderAttemptStatus] = mapped_column(
        Enum(ProviderAttemptStatus, name="ai_provider_attempt_status")
    )
    provider_response_id: Mapped[str | None] = mapped_column(String(128))
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    rate_limit_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    job: Mapped[AIJob] = relationship(back_populates="attempts")


class ProviderUsageMonth(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_provider_usage_months"
    __table_args__ = (
        Index("ix_provider_usage_account_month", "provider_account", "year_month", unique=True),
    )

    provider_account: Mapped[ProviderAccount] = mapped_column(
        Enum(ProviderAccount, name="ai_provider_usage_account")
    )
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
