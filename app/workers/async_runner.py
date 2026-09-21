"""Exactly one asyncio event loop per Celery *child* process.

Root cause this module exists to close (see the RC2 stabilization report for
the full diagnosis): every async Celery task wrapper in app/workers/tasks.py
called `asyncio.run(coro)` directly. `asyncio.run` creates a brand-new event
loop for that single call and closes it again before returning. Meanwhile
app/db/session.py's AsyncEngine (and the asyncpg connection pool underneath
it) is process-cached with `@lru_cache(maxsize=1)`, so it is created exactly
once -- bound, permanently, to whichever event loop happened to be running
the first time a task touched it -- and then silently reused by every later
task, each of which is running on a *different* loop.

asyncpg's `Connection` wraps a raw `asyncio.Transport`/`Protocol` registered
with the loop that opened it. SQLAlchemy's async connection pool eventually
needs to check a pooled connection back in or close it; the asyncpg dialect
adapter's close path (`AsyncAdapt_terminate.terminate()` in
sqlalchemy/connectors/asyncio.py) bridges that close through SQLAlchemy's
greenlet-based `await_only`, which requires a currently-running, matching
event loop context. When the loop that context expects is not the one
currently live -- exactly what happens the moment a *second* `asyncio.run()`
call starts a new loop while the pool still holds connections opened under
the *first* -- that close attempt raises, and `Pool._close_connection` (in
sqlalchemy/pool/base.py) catches it and logs precisely what production
showed:

    Exception terminating connection <AdaptedConnection <asyncpg.connection.Connection ...>>

Because the close attempt itself fails, the connection is never actually
terminated on the PostgreSQL side -- it is abandoned mid-transaction. Every
`dispatch_job_outbox` cycle (beat schedule: every 10 seconds) was a fresh
`asyncio.run()` call touching the same cached pool, so this could produce a
new orphaned, idle-in-transaction backend connection roughly every ten
seconds -- consistent with the accumulation to 37 connections observed in
production before `idle_in_transaction_session_timeout` started reaping them
as a stopgap.

The fix: create one event loop per Celery child process, after fork, and
never let more than one exist for that process's lifetime. Every async task
that process runs -- and the AsyncEngine it drives -- lives on that same
loop for as long as the process does. The engine is disposed and the loop
closed together, in that order, when the child shuts down.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import threading
from collections.abc import Coroutine
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_runner: asyncio.Runner | None = None
_runner_pid: int | None = None
_lock = threading.Lock()


def _new_runner() -> asyncio.Runner:
    # asyncio.Runner (3.11+) owns exactly one event loop for its lifetime
    # and exposes a synchronous `.run(coro)` -- the same call shape as
    # `asyncio.run`, but without creating and tearing down a loop each time.
    return asyncio.Runner()


def get_runner() -> asyncio.Runner:
    """Return this process's single Runner, creating it on first use.

    Guarded by both a PID check and a lock rather than a bare module-level
    singleton: Celery's prefork pool forks child processes *after* this
    module is imported in the parent. A Runner (and the loop/selector fd it
    opens) created before fork would be silently duplicated across every
    child, which is exactly the kind of cross-process resource confusion
    this module exists to prevent for the *engine*. Creating it lazily, on
    first actual use inside whichever process calls `run()`, combined with
    re-creating it whenever the observed PID changes, guarantees "created
    inside the child, never before fork" without depending on every possible
    Celery entrypoint (prefork, solo/eager test mode, `celery call`, a
    management shell) reliably firing `worker_process_init` first.
    `celery_app.py` still wires that signal as the primary, clean lifecycle
    hook; this is the defensive fallback under it.
    """
    global _runner, _runner_pid
    pid = os.getpid()
    with _lock:
        if _runner is None or _runner_pid != pid:
            if _runner is not None and _runner_pid != pid:
                # A Runner exists, but it belongs to the process we were
                # forked from -- its loop and selector fd are not ours to
                # use or close. Drop the reference (never call .close() on
                # it: that would touch the parent's resources from a child)
                # and create this process's own.
                logger.warning(
                    "ai_worker_runner_recreated_after_fork",
                    inherited_from_pid=_runner_pid,
                    current_pid=pid,
                )
            _runner = _new_runner()
            _runner_pid = pid
        return _runner


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` to completion on this process's single persistent loop.

    Drop-in replacement for `asyncio.run(coro)` in Celery task bodies --
    same call shape -- except the loop underneath is created once per
    process and reused, so the AsyncEngine/asyncpg pool it drives is only
    ever touched from the one loop that created it.
    """
    return get_runner().run(coro)


def close_runner() -> None:
    """Dispose this process's engine, then close its event loop.

    Idempotent. A no-op if this process never created a Runner, or if the
    Runner on record belongs to a different process (e.g. atexit firing in a
    forked child that happens to share this module's global state but never
    itself called `run()`) -- closing another process's loop would be
    exactly the cross-loop mistake this module exists to prevent.

    Order matters: the engine must be disposed *on* the loop that created
    it, before that loop is closed underneath it. Disposing first also
    means every pooled connection is given a real chance to close cleanly
    on its own loop, rather than being abandoned for Postgres's
    `idle_in_transaction_session_timeout` to eventually reap.
    """
    global _runner, _runner_pid
    pid = os.getpid()
    with _lock:
        if _runner is None or _runner_pid != pid:
            return
        runner = _runner
        _runner = None
        _runner_pid = None

    try:
        from app.db.session import dispose_engine

        runner.run(dispose_engine())
    except Exception:
        logger.exception("ai_worker_engine_dispose_failed", pid=pid)
    finally:
        runner.close()
        logger.info("ai_worker_runner_closed", pid=pid)


# Defensive backstop for process exit paths that bypass Celery's own
# worker_process_shutdown signal (killed/crashed workers, a bare `python -c`
# using this module directly, etc.). celery_app.py wires the Celery signal
# as the primary, expected path; this only fires if that never ran.
atexit.register(close_runner)
