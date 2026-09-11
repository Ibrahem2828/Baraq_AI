from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PromptVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_name_version"),
        Index("ix_prompt_active", "name", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64))
