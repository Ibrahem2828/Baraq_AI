"""Baraq_MD_Blueprint 02_AI_PLATFORM.md §12: 'the ability to delete
data/Artifacts per the privacy policy' -- distinct from the 180-day
automatic retention purge, this is an on-demand deletion path a request can
trigger for a specific user (optionally scoped to one project)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.db.base import Base
from app.models.ai_job import AIJob
from app.models.enums import Character, JobStatus, ProviderAccount, TaskType
from app.models.result_cache import CachedAIResult
from app.models.source import SourceDocument
from app.services.data_deletion import DataDeletionService


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element: object, compiler: object, **kw: object) -> str:
    return "JSON"


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deletion.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AIJob.__table__, SourceDocument.__table__, CachedAIResult.__table__],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


def _job(*, user_id: str, project_id: str | None) -> AIJob:
    return AIJob(
        id=uuid.uuid4(),
        user_id=user_id,
        project_id=project_id,
        request_id=str(uuid.uuid4()),
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        character=Character.FAHES,
        status=JobStatus.COMPLETED,
        idempotency_hash=uuid.uuid4().hex,
        input_hash=uuid.uuid4().hex,
    )


def _document(*, user_id: str, project_id: str | None, backend_source_id: str) -> SourceDocument:
    return SourceDocument(
        backend_source_id=backend_source_id,
        user_id=user_id,
        project_id=project_id,
        title="doc",
        mime_type="application/pdf",
        content_sha256="a" * 64,
    )


def _cached_result(*, user_id: str, fingerprint: str) -> CachedAIResult:
    return CachedAIResult(
        fingerprint=fingerprint,
        user_id=user_id,
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        result_json={"quiz": "content"},
        source_model_name="gpt-5-mini",
        source_provider_account=ProviderAccount.PRIMARY,
    )


@pytest.mark.asyncio
async def test_delete_user_data_only_removes_that_user(session: AsyncSession) -> None:
    session.add_all(
        [
            _job(user_id="user-a", project_id="project-1"),
            _job(user_id="user-b", project_id="project-1"),
        ]
    )
    await session.commit()

    report = await DataDeletionService(session).delete_user_data(user_id="user-a")

    assert report.jobs_deleted == 1
    remaining = (await session.execute(AIJob.__table__.select())).fetchall()
    assert len(remaining) == 1
    assert remaining[0].user_id == "user-b"


@pytest.mark.asyncio
async def test_delete_user_data_scoped_to_one_project_leaves_other_projects_intact(
    session: AsyncSession,
) -> None:
    session.add_all(
        [
            _job(user_id="user-a", project_id="project-1"),
            _job(user_id="user-a", project_id="project-2"),
            _document(user_id="user-a", project_id="project-1", backend_source_id="s-1"),
            _document(user_id="user-a", project_id="project-2", backend_source_id="s-2"),
        ]
    )
    await session.commit()

    report = await DataDeletionService(session).delete_user_data(
        user_id="user-a", project_id="project-1"
    )

    assert report.jobs_deleted == 1
    assert report.source_documents_deleted == 1
    remaining_jobs = (await session.execute(AIJob.__table__.select())).fetchall()
    assert [row.project_id for row in remaining_jobs] == ["project-2"]
    remaining_docs = (await session.execute(SourceDocument.__table__.select())).fetchall()
    assert [row.project_id for row in remaining_docs] == ["project-2"]


@pytest.mark.asyncio
async def test_delete_user_data_always_clears_cached_results_regardless_of_project(
    session: AsyncSession,
) -> None:
    # CachedAIResult carries no project_id (the fingerprint already encodes
    # the full request content) -- a project-scoped deletion request must
    # still clear it, or a stale cache hit could resurface deleted material
    # under the same fingerprint.
    session.add(_cached_result(user_id="user-a", fingerprint="f" * 64))
    await session.commit()

    report = await DataDeletionService(session).delete_user_data(
        user_id="user-a", project_id="project-1"
    )

    assert report.cached_results_deleted == 1
