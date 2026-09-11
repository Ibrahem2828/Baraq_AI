from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProviderAccount, TaskType


class CachedAIResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A reusable pipeline result, keyed by a deterministic fingerprint of
    (user, task_type, validated input + model policy, source content
    versions, prompt checksum, pipeline version) -- see
    app/services/result_cache.py. Distinct from AIJob.idempotency_hash: that
    boundary is per client_job_id resubmission; this is per *content*, so a
    different client_job_id (or a different job entirely) for the exact
    same effective request reuses the same generated result instead of
    paying for another provider call."""

    __tablename__ = "ai_cached_results"

    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="ai_task_type"), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    groundedness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    security_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    validation_status: Mapped[str] = mapped_column(String(32), default="valid", nullable=False)
    source_model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_provider_account: Mapped[ProviderAccount] = mapped_column(
        Enum(ProviderAccount, name="ai_provider_account"), nullable=False
    )
    source_estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
