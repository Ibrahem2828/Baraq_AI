from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    Character,
    DispatchOutboxStatus,
    JobStatus,
    ProviderAccount,
    ProviderAttemptStatus,
    TaskType,
)


class AIJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        Index("ix_ai_jobs_user_created", "user_id", "created_at"),
        Index("ix_ai_jobs_task_status", "task_type", "status"),
        Index("ix_ai_jobs_project_created", "project_id", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # Blueprint 01_BACKEND.md §5.2 / 02_AI_PLATFORM.md §3.2: nullable because
    # the deprecated V1 Django contract predates project scoping entirely;
    # every V2 job carries one, since Django's own AIJobCreateSerializer now
    # requires a project for every task type.
    project_id: Mapped[str | None] = mapped_column(String(64))
    backend_request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="ai_task_type"), nullable=False)
    character: Mapped[Character] = mapped_column(
        Enum(Character, name="ai_character"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="ai_job_status"), default=JobStatus.QUEUED, index=True
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str] = mapped_column(String(255), default="تم استلام الطلب")
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source_versions: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    prompt_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    prompt_checksum: Mapped[str | None] = mapped_column(String(64))
    pipeline_version: Mapped[str | None] = mapped_column(String(32))
    model_tier: Mapped[str | None] = mapped_column(String(32))
    allow_fallback: Mapped[bool] = mapped_column(default=True, nullable=False)
    credit_reservation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    output: Mapped[AIOutput | None] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    attempts: Mapped[list[ProviderAttempt]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    dispatch_events: Mapped[list[JobDispatchOutboxEvent]] = relationship(
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
    request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    materialized_resource_type: Mapped[str | None] = mapped_column(String(64))
    materialized_resource_id: Mapped[str | None] = mapped_column(String(128))
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    security_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    job: Mapped[AIJob] = relationship(back_populates="output")


class JobDispatchOutboxEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Durable, at-least-once handoff from the job transaction to Celery."""

    __tablename__ = "ai_job_dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("job_id", "event_type", name="uq_ai_job_dispatch_outbox_job_event"),
        Index("ix_ai_job_dispatch_outbox_pending", "status", "available_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), default="process_ai_job", nullable=False)
    # Which Celery queue this event's ``process_ai_job`` dispatch is sent to
    # (see ``app.models.enums.TASK_QUEUE``). Denormalized onto the event at
    # creation time -- when the job's task_type is already in hand -- so the
    # dispatcher never needs an extra DB round-trip to route it correctly.
    # Irrelevant for the ``deliver_result_webhook`` event type, which always
    # goes to ``ai_background`` regardless of this column.
    target_queue: Mapped[str] = mapped_column(String(32), default="ai_interactive", nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    status: Mapped[DispatchOutboxStatus] = mapped_column(
        Enum(DispatchOutboxStatus, name="ai_dispatch_outbox_status"),
        default=DispatchOutboxStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[AIJob] = relationship(back_populates="dispatch_events")


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
    request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
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
    # Sum of in-flight ProviderBudgetService.reserve() calls not yet released
    # or committed -- included in the budget check so concurrent workers
    # cannot each pass a stale "committed so far" check and jointly overspend
    # before any of them finishes and records real cost.
    reserved_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
