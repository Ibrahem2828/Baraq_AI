"""Async SQLAlchemy engine/session lifecycle.

Explicit, not hidden behind `lru_cache` alone: the caching here still exists
(an AsyncEngine and its connection pool are expensive to create and must be
process-singleton), but process/loop ownership is now a documented contract
rather than something a reader has to infer.

**The engine created by `get_engine()` must only ever be used from the one
event loop that was running the first time it was created**, for the
lifetime of that engine. In the FastAPI process this is trivially true --
uvicorn runs one loop for the process's whole life. In a Celery worker
process it is true *only* because `app/workers/async_runner.py` gives each
prefork child exactly one persistent loop and every task runs on it; see
that module's docstring for the failure this replaced (a cached engine
silently reused across a fresh loop per `asyncio.run()` call, which is what
produced production's leaked, idle-in-transaction connections).

`dispose_engine()` is the other half of that contract: it must be called,
on the same loop, before the process that created the engine exits, and it
must be safe to call more than once (idempotent) since both a normal
shutdown path and a defensive `atexit` backstop may each try.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
# RLock, not Lock: get_session_factory() acquires this and then calls
# get_engine(), which acquires it again on the same thread. A plain
# threading.Lock is not reentrant -- that nested acquisition would deadlock
# every caller forever. (Caught by the full test suite, not by any of the
# targeted unit tests added for this change -- see the RC2 report.)
_lock = threading.RLock()


def get_engine() -> AsyncEngine:
    """Return the process-singleton AsyncEngine, creating it on first call.

    See this module's docstring: the engine, once created, is bound to
    whichever event loop is running at that moment for as long as the
    process lives. Do not call this before the process's one persistent
    loop (FastAPI's uvicorn loop, or a Celery child's async_runner loop) is
    the loop actually running.
    """
    global _engine
    with _lock:
        if _engine is None:
            settings = get_settings()
            _engine = create_async_engine(
                settings.database_url,
                pool_pre_ping=True,
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_pool_max_overflow,
                pool_recycle=settings.db_pool_recycle_seconds,
                pool_timeout=settings.db_pool_timeout_seconds,
            )
        return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    with _lock:
        if _session_factory is None:
            _session_factory = async_sessionmaker(
                get_engine(), expire_on_commit=False, class_=AsyncSession
            )
        return _session_factory


async def dispose_engine() -> None:
    """Dispose the process-singleton engine. Idempotent; safe to call when
    no engine was ever created.

    Must be awaited on the same loop the engine was created on (see this
    module's docstring) -- `AsyncEngine.dispose()` closes every pooled
    connection, and closing an asyncio-based DBAPI connection is itself a
    coroutine that has to run on the loop that owns it.
    """
    global _engine, _session_factory
    with _lock:
        engine = _engine
        _engine = None
        _session_factory = None
    if engine is not None:
        await engine.dispose()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class _SessionFactoryProxy:
    """Compatibility proxy used by workers while keeping engine creation lazy."""

    def __call__(self) -> AsyncSession:
        return get_session_factory()()


AsyncSessionLocal = _SessionFactoryProxy()
