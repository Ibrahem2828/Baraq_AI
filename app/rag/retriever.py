from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag.embeddings import EmbeddingService
from app.rag.guard import sanitize_untrusted_source
from app.rag.repository import RetrievedChunk, SourceRepository
from app.rag.reranker import HybridReranker
from app.schemas.common import Citation


@dataclass(frozen=True, slots=True)
class RAGContext:
    text: str
    citations: list[Citation]
    chunks: list[RetrievedChunk]
    suspicious_source_detected: bool


class RAGRetriever:
    def __init__(
        self,
        *,
        session: AsyncSession,
        embeddings: EmbeddingService,
    ) -> None:
        self.settings = get_settings()
        self.repository = SourceRepository(session)
        self.embeddings = embeddings
        self.reranker = HybridReranker()

    async def retrieve(
        self,
        *,
        user_id: str,
        project_id: str | None,
        source_ids: list[str],
        source_versions: dict[str, str],
        query: str,
        routing_key: str,
    ) -> RAGContext:
        query_vector = await self.embeddings.embed_query(query, routing_key)
        chunks = await self.repository.retrieve(
            user_id=user_id,
            project_id=project_id,
            source_ids=source_ids,
            source_versions=source_versions,
            query_embedding=query_vector,
            limit=self.settings.rag_top_k,
            min_similarity=self.settings.rag_min_similarity,
        )
        chunks = self.reranker.rerank(
            query=query,
            chunks=chunks,
            limit=self.settings.rag_rerank_top_k,
        )
        context_parts: list[str] = []
        citations: list[Citation] = []
        suspicious = False
        consumed = 0
        for ref_no, chunk in enumerate(chunks, start=1):
            guarded = sanitize_untrusted_source(chunk.text)
            suspicious = suspicious or guarded.suspicious
            header = f"[S{ref_no}] source_id={chunk.source_id}"
            if chunk.page_number:
                header += f" page={chunk.page_number}"
            if chunk.section_title:
                header += f" section={chunk.section_title}"
            block = f"{header}\n{guarded.safe_text}"
            if consumed + len(block) > self.settings.rag_max_context_chars:
                break
            context_parts.append(block)
            consumed += len(block)
            citations.append(
                Citation(
                    evidence_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    content_sha256=chunk.content_sha256,
                    chunk_id=chunk.chunk_id,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    excerpt=chunk.text[:1200],
                    relevance_score=chunk.score,
                    semantic_score=chunk.semantic_score,
                    lexical_score=chunk.lexical_score,
                    rerank_score=chunk.rerank_score,
                )
            )
        return RAGContext("\n\n".join(context_parts), citations, chunks, suspicious)
