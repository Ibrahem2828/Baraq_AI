from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_job import AIJob
from app.models.result_cache import CachedAIResult
from app.models.source import SourceDocument


@dataclass(frozen=True, slots=True)
class DeletionReport:
    jobs_deleted: int
    source_documents_deleted: int
    cached_results_deleted: int


class DataDeletionService:
    """Baraq_MD_Blueprint 02_AI_PLATFORM.md §12: "إمكانية حذف بيانات/Artifacts
    حسب سياسة الخصوصية" -- an on-demand deletion path, distinct from the
    180-day retention job (app/workers/tasks.cleanup_expired_data), which is
    a background purge, not something a user/backend request can trigger.

    Deletes at the database level (not ORM cascade) so every dependent row
    (AIOutput, ProviderAttempt, JobDispatchOutboxEvent, SourceChunk) is
    removed via each table's own ON DELETE CASCADE, without loading rows
    into the session first.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def delete_user_data(
        self, *, user_id: str, project_id: str | None = None
    ) -> DeletionReport:
        async with self.session.begin():
            job_stmt = delete(AIJob).where(AIJob.user_id == user_id)
            if project_id is not None:
                job_stmt = job_stmt.where(AIJob.project_id == project_id)
            jobs_result = await self.session.execute(job_stmt)

            document_stmt = delete(SourceDocument).where(SourceDocument.user_id == user_id)
            if project_id is not None:
                document_stmt = document_stmt.where(SourceDocument.project_id == project_id)
            documents_result = await self.session.execute(document_stmt)

            cache_stmt = delete(CachedAIResult).where(CachedAIResult.user_id == user_id)
            if project_id is not None:
                cache_stmt = cache_stmt.where(CachedAIResult.project_id == project_id)
            cache_result = await self.session.execute(cache_stmt)
        return DeletionReport(
            jobs_deleted=cast("CursorResult[Any]", jobs_result).rowcount or 0,
            source_documents_deleted=cast("CursorResult[Any]", documents_result).rowcount or 0,
            cached_results_deleted=cast("CursorResult[Any]", cache_result).rowcount or 0,
        )
