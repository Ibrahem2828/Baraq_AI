from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ValidationFailure
from app.models.enums import SourceStatus
from app.rag.chunker import ArabicAwareChunker
from app.rag.embeddings import EmbeddingService
from app.rag.extractors import DocumentExtractor
from app.rag.repository import SourceRepository
from app.schemas.backend import SourceManifest
from app.services.backend_client import BackendClient
from app.utils.hash import sha256_bytes


class SourceIngestionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        backend: BackendClient,
        embeddings: EmbeddingService,
    ) -> None:
        self.settings = get_settings()
        self.session = session
        self.backend = backend
        self.embeddings = embeddings
        self.repository = SourceRepository(session)
        self.extractor = DocumentExtractor()
        self.chunker = ArabicAwareChunker(
            chunk_size=self.settings.rag_chunk_size_chars,
            overlap=self.settings.rag_chunk_overlap_chars,
        )

    async def ensure_ingested(self, *, source_id: str, user_id: str) -> SourceManifest:
        manifest = await self.backend.get_source_manifest(source_id=source_id, user_id=user_id)
        existing = await self.repository.get_document_version(
            backend_source_id=manifest.source_id,
            content_sha256=manifest.content_sha256,
        )
        if existing and existing.status == SourceStatus.READY:
            return manifest

        if manifest.size_bytes > self.settings.max_source_file_bytes:
            raise ValidationFailure(
                "Source file exceeds the configured size limit",
                code="source_too_large",
            )
        content = await self.backend.download_source(manifest)
        if len(content) != manifest.size_bytes:
            raise ValidationFailure(
                "Downloaded source size does not match the backend manifest",
                code="source_size_mismatch",
            )
        if sha256_bytes(content) != manifest.content_sha256:
            raise ValidationFailure(
                "Downloaded source checksum does not match the backend manifest",
                code="source_checksum_mismatch",
            )

        document = existing or await self.repository.create_document(
            backend_source_id=manifest.source_id,
            user_id=user_id,
            title=manifest.title,
            mime_type=manifest.mime_type,
            content_sha256=manifest.content_sha256,
            status=SourceStatus.EXTRACTING,
            metadata_json=manifest.metadata,
        )
        try:
            extracted = self.extractor.extract(
                filename=manifest.title,
                mime_type=manifest.mime_type,
                content=content,
            )
            chunks = self.chunker.chunk(extracted)
            if not chunks:
                raise ValidationFailure("No useful text chunks were extracted", code="empty_chunks")
            document.status = SourceStatus.EMBEDDING
            document.page_count = int(extracted.metadata.get("page_count") or 0) or None
            await self.session.flush()
            vectors = await self.embeddings.embed_documents(
                [chunk.text for chunk in chunks],
                routing_key=f"source:{source_id}:{manifest.content_sha256}",
            )
            if len(vectors) != len(chunks):
                raise ValidationFailure("Embedding count mismatch", code="embedding_count_mismatch")
            await self.repository.replace_chunks(
                document=document,
                chunks=[
                    {
                        "chunk_index": chunk.chunk_index,
                        "page_number": chunk.page_number,
                        "section_title": chunk.section_title,
                        "text": chunk.text,
                        "token_estimate": chunk.token_estimate,
                        "metadata_json": {},
                        "embedding": vector,
                    }
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )
            document.status = SourceStatus.READY
            await self.session.commit()
        except Exception as exc:
            document.status = SourceStatus.FAILED
            document.extraction_error = str(exc)[:4000]
            await self.session.commit()
            raise
        return manifest
