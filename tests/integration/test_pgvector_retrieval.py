"""Real pgvector retrieval, against a real PostgreSQL.

Everything else in this suite can run without a database. This file cannot
and should not: the whole point is to prove that similarity search actually
returns the right chunk, and that the tenant filter in the query is applied
by the database rather than by the caller afterwards. A mocked repository
proves neither.

It skips unless `BARAQ_AI_TEST_DATABASE_URL` names a disposable PostgreSQL
with the `vector` extension available. That variable is deliberately not
`database_url`: pointing this at the service's configured database would
mean creating and deleting rows in whatever environment happens to be
configured, which is exactly the accident a release test should not have.

Embeddings are deterministic fixtures, not provider calls. The gate here is
the storage and retrieval path; paying a provider to re-prove that cosine
distance works would add cost and flakiness and prove nothing extra. The
provider path has its own smoke test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.source import SourceChunk, SourceDocument
from app.rag.repository import SourceRepository

TEST_DATABASE_URL = os.environ.get("BARAQ_AI_TEST_DATABASE_URL", "").strip()

pytestmark = [
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason=(
            "Set BARAQ_AI_TEST_DATABASE_URL to a disposable PostgreSQL with the "
            "vector extension to run real retrieval tests."
        ),
    ),
    pytest.mark.asyncio,
]

# A fact that exists nowhere else, so a retrieval hit cannot be a coincidence
# or a substring match against boilerplate.
ARABIC_FACT = "يتكون بروتوكول التحقق التجريبي لبرّاق من سبع مراحل مستقلة."
ARABIC_FILLER = [
    "تهدف منصة برّاق إلى مساعدة الطلاب على تنظيم دراستهم بطريقة أوضح.",
    "يمكن للطالب رفع مصادره التعليمية ثم استخدامها مع الشخصيات الخمس.",
    "تعتمد الخطة الدراسية على وقت الطالب المتاح وعلى أهدافه القريبة.",
    "تُحفظ نتائج التحليل حتى يتمكن الطالب من مراجعتها لاحقاً.",
]

DIMENSIONS = 1536


def embed(marker: int) -> list[float]:
    """A deterministic unit-ish vector that is close only to itself.

    One dominant axis per marker: cosine distance between two different
    markers is then large, and between a query and its own chunk is zero.
    That makes the assertions about *which* chunk came back meaningful
    without involving a provider.
    """
    vector = [0.0] * DIMENSIONS
    vector[marker % DIMENSIONS] = 1.0
    return vector


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active

    # Disposable by construction: the schema goes when the test does.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def seed_document(
    session: AsyncSession,
    *,
    user_id: str,
    project_id: str,
    source_id: str,
    texts: Sequence[str],
    first_marker: int,
) -> SourceDocument:
    """One document and its chunks, each chunk with a distinct embedding."""
    document = SourceDocument(
        user_id=user_id,
        project_id=project_id,
        backend_source_id=source_id,
        content_sha256=f"sha-{source_id}",
    )
    session.add(document)
    await session.flush()

    for offset, body in enumerate(texts):
        session.add(
            SourceChunk(
                document_id=document.id,
                chunk_index=offset,
                content=body,
                embedding=embed(first_marker + offset),
            )
        )
    await session.commit()
    return document


class TestArabicRetrieval:
    """PART F: the fact has to actually come back."""

    async def test_the_unique_arabic_fact_is_retrieved(self, session: AsyncSession) -> None:
        user_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        # The fact sits among filler, at a known position.
        texts = [*ARABIC_FILLER[:2], ARABIC_FACT, *ARABIC_FILLER[2:]]
        fact_index = texts.index(ARABIC_FACT)

        await seed_document(
            session,
            user_id=user_id,
            project_id=project_id,
            source_id=source_id,
            texts=texts,
            first_marker=100,
        )

        results = await SourceRepository(session).retrieve(
            user_id=user_id,
            project_id=project_id,
            source_ids=[source_id],
            source_versions={source_id: f"sha-{source_id}"},
            query_embedding=embed(100 + fact_index),
            limit=3,
            min_similarity=0.2,
        )

        assert results, "similarity search returned nothing for a chunk that exists"
        assert ARABIC_FACT in results[0].text, (
            "the nearest chunk was not the one holding the fact; "
            f"got: {results[0].text[:60]!r}"
        )
        # Arabic survives the round trip through the column, not just the query.
        assert "سبع مراحل" in results[0].text

    async def test_version_pinning_excludes_a_superseded_document(
        self, session: AsyncSession
    ) -> None:
        """A job must read the file hash it was created with, not a newer one."""
        user_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        await seed_document(
            session,
            user_id=user_id,
            project_id=project_id,
            source_id=source_id,
            texts=[ARABIC_FACT],
            first_marker=200,
        )

        results = await SourceRepository(session).retrieve(
            user_id=user_id,
            project_id=project_id,
            source_ids=[source_id],
            # The hash the job was pinned to is not the one stored.
            source_versions={source_id: "sha-of-a-different-version"},
            query_embedding=embed(200),
            limit=5,
            min_similarity=0.0,
        )

        assert results == [], "a job retrieved content from a version it was not pinned to"


class TestRetrievalIsolation:
    """PART G: the tenant filter has to be in the query."""

    async def test_one_learner_never_retrieves_another(self, session: AsyncSession) -> None:
        project_a, project_b = str(uuid.uuid4()), str(uuid.uuid4())
        user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
        source_a, source_b = str(uuid.uuid4()), str(uuid.uuid4())

        # Identical content and identical embeddings on both sides. If the
        # filter were applied after ranking, B's chunk would score exactly as
        # well as A's and the leak would be invisible to a similarity check.
        for user_id, project_id, source_id in (
            (user_a, project_a, source_a),
            (user_b, project_b, source_b),
        ):
            await seed_document(
                session,
                user_id=user_id,
                project_id=project_id,
                source_id=source_id,
                texts=[ARABIC_FACT],
                first_marker=300,
            )

        repository = SourceRepository(session)

        mine = await repository.retrieve(
            user_id=user_a,
            project_id=project_a,
            source_ids=[source_a],
            source_versions={source_a: f"sha-{source_a}"},
            query_embedding=embed(300),
            limit=10,
            min_similarity=0.0,
        )
        assert len(mine) == 1
        assert mine[0].source_id == source_a

        # Naming the other learner's source explicitly, which is what a
        # tampered job payload would do.
        theirs = await repository.retrieve(
            user_id=user_a,
            project_id=project_a,
            source_ids=[source_a, source_b],
            source_versions={
                source_a: f"sha-{source_a}",
                source_b: f"sha-{source_b}",
            },
            query_embedding=embed(300),
            limit=10,
            min_similarity=0.0,
        )
        assert {row.source_id for row in theirs} == {source_a}, (
            "a source id in the payload reached another learner's document"
        )

    async def test_the_same_project_id_under_a_different_user_is_still_refused(
        self, session: AsyncSession
    ) -> None:
        """Guessing an identifier must not be enough.

        Both filters are in the same WHERE clause, so this would only fail if
        one of them were dropped -- which is precisely the regression worth
        catching.
        """
        shared_project = str(uuid.uuid4())
        user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
        source_b = str(uuid.uuid4())

        await seed_document(
            session,
            user_id=user_b,
            project_id=shared_project,
            source_id=source_b,
            texts=[ARABIC_FACT],
            first_marker=400,
        )

        results = await SourceRepository(session).retrieve(
            user_id=user_a,
            project_id=shared_project,
            source_ids=[source_b],
            source_versions={source_b: f"sha-{source_b}"},
            query_embedding=embed(400),
            limit=10,
            min_similarity=0.0,
        )

        assert results == [], "knowing a project id was enough to read another user's chunks"
