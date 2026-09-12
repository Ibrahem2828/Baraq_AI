from __future__ import annotations

from app.schemas.common import StrictModel


class DataDeletionResult(StrictModel):
    jobs_deleted: int
    source_documents_deleted: int
    cached_results_deleted: int
