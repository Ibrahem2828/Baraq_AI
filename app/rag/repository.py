from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import SourceChunk, SourceDocument


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    source_id: str
    content_sha256: str
    title: str
    page_number: int | None
    section_title: str | None
    text: str
    score: float
    semantic_score: float
    lexical_score: float = 0.0
    rerank_score: float = 0.0


class SourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_document_version(
        self, *, backend_source_id: str, content_sha256: str
    ) -> SourceDocument | None:
        stmt = select(SourceDocument).where(
            SourceDocument.backend_source_id == backend_source_id,
            SourceDocument.content_sha256 == content_sha256,
        )
        return cast(SourceDocument | None, await self.session.scalar(stmt))

    async def create_document(self, **values: object) -> SourceDocument:
        document = SourceDocument(**values)
        self.session.add(document)
        await self.session.flush()
        return document

    async def replace_chunks(
        self,
        *,
        document: SourceDocument,
        chunks: list[dict[str, object]],
    ) -> None:
        await self.session.execute(
            delete(SourceChunk).where(SourceChunk.document_id == document.id)
        )
        for chunk in chunks:
            self.session.add(SourceChunk(document_id=document.id, **chunk))
        await self.session.flush()

    async def retrieve(
        self,
        *,
        user_id: str,
        source_ids: list[str],
        source_versions: dict[str, str],
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedChunk]:
        distance = SourceChunk.embedding.cosine_distance(query_embedding)
        stmt: Select[tuple[SourceChunk, SourceDocument, float]] = (
            select(SourceChunk, SourceDocument, distance.label("distance"))
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(
                SourceDocument.user_id == user_id,
                SourceDocument.backend_source_id.in_(source_ids),
                SourceChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        if source_versions:
            # Source identifiers alone are not a stable authorization scope:
            # each job must retrieve only the file hash captured at creation.
            from sqlalchemy import or_

            stmt = stmt.where(
                or_(
                    *[
                        (SourceDocument.backend_source_id == source_id)
                        & (SourceDocument.content_sha256 == content_hash)
                        for source_id, content_hash in source_versions.items()
                    ]
                )
            )
        rows = (await self.session.execute(stmt)).all()
        results: list[RetrievedChunk] = []
        for chunk, document, dist in rows:
            score = max(0.0, min(1.0, 1.0 - float(dist)))
            if score < min_similarity:
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    source_id=document.backend_source_id,
                    content_sha256=document.content_sha256,
                    title=document.title,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    text=chunk.text,
                    score=score,
                    semantic_score=score,
                )
            )
        return results
