"""RC2 diagnostic: repeated worker-shaped task cycles must not leak connections.

This is the direct reproduction of the production defect (see the RC2
stabilization report): 37 idle-in-transaction connections accumulated from
a single AI worker process, at a cadence matching `dispatch_job_outbox`'s
10-second beat schedule, with PostgreSQL logging
`Exception terminating connection <AdaptedConnection <asyncpg.connection.Connection ...>>`
approximately every few tens of seconds.

Every other test in this suite either mocks the database entirely or opens
one session and closes it within a single event loop (pytest-asyncio's own
loop for that test). Neither proves anything about *this* bug, which is
specifically about what happens to a process-cached engine across multiple,
separate event loops -- which is exactly what a Celery prefork child's
sequence of task invocations used to look like before
app/workers/async_runner.py existed, and what it looks like now that it
does.

This test drives `app.db.session` and `app.workers.async_runner` -- the real
modules the fix lives in, not a rebuilt stand-in -- through many separate
`async_runner.run()` calls against a real PostgreSQL, and inspects
`pg_stat_activity` directly rather than trusting the application's own
account of its connection count.

Skips unless `BARAQ_AI_TEST_DATABASE_URL` names a disposable PostgreSQL, for
the same reason tests/integration/test_pgvector_retrieval.py does: this
creates and drops rows, and must never run against whatever `DATABASE_URL`
happens to be configured for a real deployment.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.workers import async_runner

TEST_DATABASE_URL = os.environ.get("BARAQ_AI_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "Set BARAQ_AI_TEST_DATABASE_URL to a disposable PostgreSQL to run the "
        "real worker connection-lifecycle diagnostic."
    ),
)

# High enough to make an unbounded leak unmistakable (each iteration used to
# be able to orphan one connection), low enough to keep the test fast.
TASK_CYCLES = 25


def _application_name() -> str:
    return "baraq_ai_rc2_lifecycle_test"


async def _bootstrap_schema() -> None:
    """Create the schema once, through a throwaway direct engine (not the
    module-under-test's cached one -- that would pre-empt the very thing each
    test is about to exercise for real)."""
    bootstrap_engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with bootstrap_engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await bootstrap_engine.dispose()


async def _backends_for_this_test() -> list[dict[str, Any]]:
    """Inspect from a fresh, separate connection -- not through the engine
    under test, so the inspection query itself cannot be mistaken for one of
    the connections it is counting."""
    inspector_engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with inspector_engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT pid, state, xact_start, query "
                    "FROM pg_stat_activity "
                    "WHERE application_name = :app_name"
                ),
                {"app_name": _application_name()},
            )
            return [dict(row._mapping) for row in rows]
    finally:
        await inspector_engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_worker_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point app.db.session at the disposable test database instead of
    whatever `settings.database_url` resolves to, and guarantee a clean
    engine/runner slate before and after -- this test's whole point is to
    observe engine creation and disposal, so it must not inherit state left
    behind by an earlier test module."""
    from app.core.config import get_settings
    from app.db import session as db_session

    async_runner.close_runner()
    db_session._engine = None
    db_session._session_factory = None

    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)

    yield

    async_runner.close_runner()
    db_session._engine = None
    db_session._session_factory = None


# Deliberately synchronous, like the Celery task wrappers in
# app/workers/tasks.py: async_runner.run() drives its own persistent loop and
# cannot be called from inside a running one (pytest-asyncio's included).
# Bootstrap and inspection each use a short-lived engine on its own
# asyncio.run() loop, so neither touches the engine under test.
def test_repeated_worker_task_cycles_leave_no_idle_in_transaction_connections() -> None:
    from app.db.session import AsyncSessionLocal, dispose_engine, get_engine

    asyncio.run(_bootstrap_schema())

    async def one_task_cycle(marker: int) -> None:
        """Shaped like a real Celery task body: get_engine()'s process-cached
        engine (through the real app.db.session module), open a session,
        touch the database, commit -- then return, exactly like
        `dispatch_job_outbox`/`process_ai_job`/etc. do in
        app/workers/tasks.py."""
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text(f"SET application_name = '{_application_name()}'"))
            await connection.commit()
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT CAST(:marker AS integer)"), {"marker": marker})
            await session.commit()

    # This is the actual regression reproduction: each iteration goes
    # through async_runner.run(), exactly as app/workers/tasks.py's
    # `@celery_task` wrappers do -- not asyncio.run(), which is the pattern
    # that leaked in production.
    for cycle in range(TASK_CYCLES):
        async_runner.run(one_task_cycle(cycle))

    backends = asyncio.run(_backends_for_this_test())

    idle_in_transaction = [b for b in backends if b["state"] == "idle in transaction"]

    assert idle_in_transaction == [], (
        f"{len(idle_in_transaction)} connection(s) left idle in transaction "
        f"after {TASK_CYCLES} worker task cycles: {idle_in_transaction}. "
        "This is the exact production defect -- a transaction that was "
        "never committed or rolled back because closing the connection "
        "that opened it failed across an event-loop boundary."
    )

    # The other half of the acceptance bar: connection *count* itself
    # must not have grown with the number of cycles run. A small, bounded
    # number of backends (the pool's configured size, not TASK_CYCLES) is
    # correct; TASK_CYCLES of them would mean the pool is not being
    # reused at all -- itself a symptom of a fresh engine per call.
    assert len(backends) < TASK_CYCLES, (
        f"{len(backends)} backend(s) open after {TASK_CYCLES} cycles -- "
        "connection count grew with the number of tasks run instead of "
        "staying bounded by the pool size, which is exactly the "
        "'0 -> 5 -> 15 -> 30 -> 50' progressive-accumulation pattern "
        "production showed."
    )

    # Final cleanup half of the acceptance bar: disposing the engine (as
    # worker_process_shutdown does for a real worker, on the runner's own
    # loop) must not itself throw -- the whole class of bug this test exists
    # for was exactly a close attempt failing and logging "Exception
    # terminating connection". close_runner() would log and swallow that, so
    # the dispose is run directly here to let it fail the test.
    async_runner.run(dispose_engine())


def test_dispose_engine_after_many_cycles_closes_every_pooled_connection() -> None:
    """A stronger version of the same acceptance bar: after dispose_engine(),
    PostgreSQL should show *zero* backends under this test's application_name
    at all -- not just zero idle-in-transaction ones."""
    from app.db.session import AsyncSessionLocal, dispose_engine, get_engine

    asyncio.run(_bootstrap_schema())

    async def one_task_cycle() -> None:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text(f"SET application_name = '{_application_name()}'"))
            await connection.commit()
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            await session.commit()

    for _ in range(10):
        async_runner.run(one_task_cycle())

    async_runner.run(dispose_engine())

    remaining = len(asyncio.run(_backends_for_this_test()))
    assert remaining == 0, (
        f"{remaining} connection(s) still visible in pg_stat_activity after "
        "dispose_engine() -- the pool did not actually close them."
    )
