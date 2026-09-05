from __future__ import annotations

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SourceStatus

settings = get_settings()


class SourceDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_source_documents"
    __table_args__ = (
        UniqueConstraint("backend_source_id", "content_sha256", name="uq_source_version"),
        Index("ix_source_user_backend", "user_id", "backend_source_id"),
    )

    backend_source_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(150))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus, name="ai_source_status"), default=SourceStatus.PENDING
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16), default="ar")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extraction_error: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[SourceChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class SourceChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_source_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        Index("ix_chunk_document_page", "document_id", "page_number"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_source_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=True
    )

    document: Mapped[SourceDocument] = relationship(back_populates="chunks")
