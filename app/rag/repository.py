from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import SourceChunk, SourceDocument
from app.rag.outline import label_units, unit_label


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
        self, *, user_id: str, project_id: str, backend_source_id: str, content_sha256: str
    ) -> SourceDocument | None:
        stmt = select(SourceDocument).where(
            SourceDocument.user_id == user_id,
            SourceDocument.project_id == project_id,
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
        project_id: str,
        source_ids: list[str],
        source_versions: dict[str, str],
        query_embedding: list[float],
        limit: int,
        min_similarity: float,
    ) -> list[RetrievedChunk]:
        distance = SourceChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(SourceChunk, SourceDocument, distance.label("distance"))
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(
                SourceDocument.user_id == user_id,
                SourceDocument.project_id == project_id,
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
        for row in rows:
            # Typed per element: SQLAlchemy 2.0 and 2.1 type result rows differently.
            chunk = cast(SourceChunk, row[0])
            document = cast(SourceDocument, row[1])
            score = max(0.0, min(1.0, 1.0 - float(row[2])))
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

    async def sample_across(
        self,
        *,
        user_id: str,
        project_id: str,
        source_ids: list[str],
        source_versions: dict[str, str],
        limit: int,
        units: set[int] | None = None,
        skip_leading_fraction: float = 0.05,
    ) -> list[RetrievedChunk]:
        chunks, _ = await self.sample_scoped(
            user_id=user_id,
            project_id=project_id,
            source_ids=source_ids,
            source_versions=source_versions,
            limit=limit,
            units=units,
            skip_leading_fraction=skip_leading_fraction,
        )
        return chunks

    async def sample_scoped(
        self,
        *,
        user_id: str,
        project_id: str,
        source_ids: list[str],
        source_versions: dict[str, str],
        limit: int,
        units: set[int] | None = None,
        skip_leading_fraction: float = 0.05,
    ) -> tuple[list[RetrievedChunk], bool]:
        """Chunks spread evenly over the selected sources, in reading order.

        For a request about a whole source (no topic), ranking against a
        generic stand-in query favoured front matter -- a textbook's cover,
        authoring committee and table of contents. Where the source has unit
        headers (app/rag/outline.py), front matter is what precedes the first
        unit and is left out, and ``units`` restricts the sample to the units a
        learner asked for; otherwise the opening fraction of a long source is
        skipped. Returns the chunks and whether the requested units were found
        (True when none were requested). Tenant and version filters are the
        same as retrieve(); embeddings are never loaded.
        """
        from sqlalchemy import or_

        stmt = (
            select(SourceChunk.id, SourceChunk.text, SourceDocument.backend_source_id)
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(
                SourceDocument.user_id == user_id,
                SourceDocument.project_id == project_id,
                SourceDocument.backend_source_id.in_(source_ids),
            )
            .order_by(SourceDocument.backend_source_id, SourceChunk.chunk_index)
        )
        if source_versions:
            stmt = stmt.where(
                or_(
                    *[
                        (SourceDocument.backend_source_id == source_id)
                        & (SourceDocument.content_sha256 == content_hash)
                        for source_id, content_hash in source_versions.items()
                    ]
                )
            )
        rows = list((await self.session.execute(stmt)).all())
        if not rows or limit <= 0:
            return [], not units
        # Units are labelled per source: a unit never carries into the next file.
        labels: dict[uuid.UUID, int | None] = {}
        by_source: dict[str, list[tuple[uuid.UUID, str]]] = {}
        for chunk_id, text, source_id in rows:
            by_source.setdefault(source_id, []).append((chunk_id, text))
        for items in by_source.values():
            texts = [text for _, text in items]
            for (chunk_id, _), label in zip(items, label_units(texts), strict=True):
                labels[chunk_id] = label
        ordered = [row[0] for row in rows]
        found = True
        if any(label is not None for label in labels.values()):
            content = [chunk_id for chunk_id in ordered if labels[chunk_id] is not None]
            pool = [chunk_id for chunk_id in content if not units or labels[chunk_id] in units]
            if not pool:
                # The source has no such unit: sample all of it and say so.
                pool, found = content, False
        else:
            start = int(len(ordered) * skip_leading_fraction) if len(ordered) >= 40 else 0
            pool, found = ordered[start:], not units
        if len(pool) <= limit:
            picked = pool
        else:
            step = (len(pool) - 1) / (limit - 1) if limit > 1 else 0
            picked = [pool[round(index * step)] for index in range(limit)]
        picked_rows = (
            await self.session.execute(
                select(SourceChunk, SourceDocument)
                .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                .where(SourceChunk.id.in_(picked))
                .order_by(SourceDocument.backend_source_id, SourceChunk.chunk_index)
            )
        ).all()
        chunks: list[RetrievedChunk] = []
        for chunk, document in picked_rows:
            unit = labels.get(chunk.id)
            chunks.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    source_id=document.backend_source_id,
                    content_sha256=document.content_sha256,
                    title=document.title,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title or (unit_label(unit) if unit else None),
                    text=chunk.text,
                    score=1.0,
                    semantic_score=0.0,
                )
            )
        return chunks, found
