from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.dependencies import DbSession, DjangoService
from app.schemas.common import APIEnvelope
from app.schemas.data_deletion import DataDeletionResult
from app.services.data_deletion import DataDeletionService

router = APIRouter(prefix="/users", tags=["Django Gateway Data Deletion"])


@router.delete("/{user_id}/data", response_model=APIEnvelope[DataDeletionResult])
async def delete_user_data(
    user_id: str,
    session: DbSession,
    _: DjangoService,
    project_id: str | None = Query(default=None),
) -> APIEnvelope[DataDeletionResult]:
    """Baraq_MD_Blueprint 02_AI_PLATFORM.md §12: on-demand deletion of a
    user's (optionally one project's) AI jobs, outputs, ingested source
    content and cached results -- signed and callable only by Django, the
    same trust boundary as job creation. `user_id` is part of the signed
    canonical query string like the job GET/cancel endpoints, so it cannot
    be spoofed independently of the HMAC signature.
    """
    report = await DataDeletionService(session).delete_user_data(
        user_id=user_id, project_id=project_id
    )
    return APIEnvelope(
        data=DataDeletionResult(
            jobs_deleted=report.jobs_deleted,
            source_documents_deleted=report.source_documents_deleted,
            cached_results_deleted=report.cached_results_deleted,
        )
    )
