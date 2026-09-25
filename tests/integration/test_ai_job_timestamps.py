"""Aware UTC datetimes must bind to ai_jobs timestamps on real PostgreSQL.

The worker sets `job.started_at = datetime.now(UTC)`. With the column typed
TIMESTAMP WITHOUT TIME ZONE, asyncpg rejected that bind ("can't subtract
offset-naive and offset-aware datetimes") and every job failed on its first
status update. SQLite accepts either, so only PostgreSQL can show it.

Skips unless `BARAQ_AI_TEST_DATABASE_URL` names a disposable PostgreSQL, like
the other integration tests. It only binds parameters; it writes nothing.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import bindparam, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.models.ai_job import AIJob

TEST_DATABASE_URL = os.environ.get("BARAQ_AI_TEST_DATABASE_URL", "").strip()

pytestmark = [
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="Set BARAQ_AI_TEST_DATABASE_URL to a disposable PostgreSQL to run this test.",
    ),
    pytest.mark.asyncio,
]


@pytest.mark.parametrize("column", ["started_at", "completed_at"])
async def test_an_aware_utc_value_binds_with_the_column_type(column: str) -> None:
    column_type = AIJob.__table__.c[column].type
    now = datetime.now(UTC)
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with engine.connect() as connection:
            stored: datetime = (
                await connection.execute(select(bindparam("value", now, type_=column_type)))
            ).scalar_one()
    finally:
        await engine.dispose()
    assert stored == now
