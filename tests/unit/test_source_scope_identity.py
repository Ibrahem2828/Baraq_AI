from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.core.errors import ValidationFailure
from app.models.ai_job import AIJob
from app.models.enums import Character, JobStatus, TaskType
from app.pipelines.base import require_project_id
from app.rag.repository import SourceRepository


class _ScalarSession:
    def __init__(self) -> None:
        self.statement: Any = None

    async def scalar(self, statement: Any) -> None:
        self.statement = statement
        return None


def _job(project_id: str | None) -> AIJob:
    return AIJob(
        user_id="user-1",
        project_id=project_id,
        request_id="00000000-0000-0000-0000-000000000001",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        character=Character.FAHES,
        status=JobStatus.QUEUED,
        idempotency_hash="a" * 64,
        input_hash="b" * 64,
    )


def test_pipeline_rejects_an_unscoped_historic_job() -> None:
    with pytest.raises(ValidationFailure) as error:
        require_project_id(_job(None))
    assert error.value.code == "project_id_required"


def test_pipeline_accepts_an_explicit_project_scope() -> None:
    assert require_project_id(_job("project-a")) == "project-a"


@pytest.mark.asyncio
async def test_document_identity_lookup_contains_user_and_project_scope() -> None:
    session = _ScalarSession()
    repository = SourceRepository(session)  # type: ignore[arg-type]

    await repository.get_document_version(
        user_id="user-a",
        project_id="project-a",
        backend_source_id="source-1",
        content_sha256="c" * 64,
    )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ai_source_documents.user_id = 'user-a'" in sql
    assert "ai_source_documents.project_id = 'project-a'" in sql
    assert "ai_source_documents.backend_source_id = 'source-1'" in sql

