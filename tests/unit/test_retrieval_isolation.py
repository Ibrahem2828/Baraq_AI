"""Retrieval isolation.

Successful embeddings are not evidence that RAG is safe. These assert the
actual SQL the retriever issues, because the properties that matter are
properties of the query: a missing `user_id` predicate does not fail a
similarity search, it quietly returns someone else's material.

Compiled-SQL assertions rather than a live database, so they run in the
normal unit suite. End-to-end retrieval against PostgreSQL + pgvector is an
RC gate.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.rag.repository import SourceRepository

USER = "user-a"
OTHER_USER = "user-b"
PROJECT = "project-a"
CURRENT_SHA = "a" * 64
STALE_SHA = "b" * 64


class _ExecuteSession:
    """Captures the statement without needing a database."""

    def __init__(self) -> None:
        self.statement: Any = None

    async def execute(self, statement: Any) -> Any:
        self.statement = statement
        return _EmptyResult()

    async def scalar(self, statement: Any) -> None:
        self.statement = statement
        return None


class _EmptyResult:
    def all(self) -> list[Any]:
        return []


async def _search_sql(**overrides: Any) -> str:
    session = _ExecuteSession()
    repository = SourceRepository(session)  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {
        "user_id": USER,
        "project_id": PROJECT,
        "source_ids": ["source-1"],
        "source_versions": {"source-1": CURRENT_SHA},
        "query_embedding": [0.0] * 8,
        "limit": 5,
        "min_similarity": 0.0,
    }
    kwargs.update(overrides)
    await repository.retrieve(**kwargs)
    return str(
        session.statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_retrieval_is_scoped_to_the_owner() -> None:
    sql = await _search_sql()
    assert f"ai_source_documents.user_id = '{USER}'" in sql
    assert OTHER_USER not in sql


@pytest.mark.asyncio
async def test_retrieval_is_scoped_to_the_project() -> None:
    """A user with two projects must not retrieve across them: one learner's
    own material is still the wrong material in the wrong context."""
    sql = await _search_sql()
    assert f"ai_source_documents.project_id = '{PROJECT}'" in sql


@pytest.mark.asyncio
async def test_retrieval_is_limited_to_the_selected_sources() -> None:
    sql = await _search_sql(
        source_ids=["source-1", "source-2"],
        source_versions={"source-1": CURRENT_SHA, "source-2": CURRENT_SHA},
    )
    assert "backend_source_id IN ('source-1', 'source-2')" in sql
    assert "source-3" not in sql


@pytest.mark.asyncio
async def test_a_superseded_version_is_not_retrievable() -> None:
    """The cache-invalidation property.

    A source keeps its id when its content changes, so the id alone is not a
    safe cache key -- pinning the content hash captured at job creation is
    what stops a job retrieving the document it was not created against.
    """
    sql = await _search_sql(source_versions={"source-1": CURRENT_SHA})

    assert f"ai_source_documents.content_sha256 = '{CURRENT_SHA}'" in sql
    assert STALE_SHA not in sql


@pytest.mark.asyncio
async def test_each_source_is_pinned_to_its_own_version() -> None:
    """With several sources, the pairing matters: source-1's chunks must not
    become retrievable via source-2's hash."""
    sql = await _search_sql(
        source_ids=["source-1", "source-2"],
        source_versions={"source-1": CURRENT_SHA, "source-2": STALE_SHA},
    )

    pairing = "backend_source_id = '{0}' AND ai_source_documents.content_sha256 = '{1}'"
    assert pairing.format("source-1", CURRENT_SHA) in sql
    assert pairing.format("source-2", STALE_SHA) in sql


@pytest.mark.asyncio
async def test_the_document_identity_key_is_all_four_dimensions() -> None:
    """Ingestion reuse keys on (user, project, source, content hash). Drop any
    one and reuse becomes either a leak or a stale read."""
    session = _ExecuteSession()
    repository = SourceRepository(session)  # type: ignore[arg-type]

    await repository.get_document_version(
        user_id=USER,
        project_id=PROJECT,
        backend_source_id="source-1",
        content_sha256=CURRENT_SHA,
    )

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),  # type: ignore[no-untyped-call]
            compile_kwargs={"literal_binds": True},
        )
    )
    for predicate in (
        f"user_id = '{USER}'",
        f"project_id = '{PROJECT}'",
        "backend_source_id = 'source-1'",
        f"content_sha256 = '{CURRENT_SHA}'",
    ):
        assert predicate in sql, f"reuse key is missing {predicate}"


@pytest.mark.asyncio
async def test_only_embedded_chunks_are_considered() -> None:
    """A chunk row written before its embedding landed must not be returned
    as a silent zero-relevance match."""
    sql = await _search_sql()
    assert "ai_source_chunks.embedding IS NOT NULL" in sql
