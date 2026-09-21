"""RC2 regression: exactly one event loop per process, reused across tasks.

Root cause (see the RC2 stabilization report for the full trace to
production's "Exception terminating connection" log line): every Celery
task wrapper called `asyncio.run(coro)`, which creates a brand-new loop per
call, while app/db/session.py's AsyncEngine was process-cached and so
silently reused across every one of those different loops. asyncpg's
Connection wraps a raw asyncio Transport/Protocol bound to the loop that
created it; using -- or even just closing -- it from a different loop is
what produced the leaked, idle-in-transaction connections observed in
production.

These tests prove app/workers/async_runner.py actually closes that gap:
repeated `run()` calls execute on the *same* loop object, a loop-bound
primitive created in one `run()` call survives being used in a later one
(the same property asyncpg's Connection needs and did not have under
`asyncio.run()`), and the runner's shutdown path disposes the engine and
closes the loop exactly once, safely, even when called more than once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from app.workers import async_runner


@pytest.fixture(autouse=True)
def _reset_runner_state() -> Iterator[None]:
    """Every test in this file gets a clean slate: no leftover Runner from
    a previous test, and none left behind for the next one or for pytest's
    own event loop machinery."""
    async_runner.close_runner()
    yield
    async_runner.close_runner()


def test_run_executes_the_coroutine_and_returns_its_result() -> None:
    async def coro() -> int:
        return 42

    assert async_runner.run(coro()) == 42


def test_repeated_run_calls_execute_on_the_same_loop() -> None:
    """The core regression: under the old `asyncio.run()`-per-call pattern,
    each of these would run on a *different* loop object. A process-cached
    resource (the AsyncEngine) bound to whichever loop was running on the
    first call would then be silently reused from a different loop on every
    later call -- which is exactly the mechanism that broke in production.
    """

    async def capture_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    first_loop = async_runner.run(capture_loop())
    second_loop = async_runner.run(capture_loop())
    third_loop = async_runner.run(capture_loop())

    assert first_loop is second_loop is third_loop
    assert not first_loop.is_closed()


def test_get_runner_returns_the_same_runner_object_within_a_process() -> None:
    first = async_runner.get_runner()
    second = async_runner.get_runner()
    assert first is second


def test_a_loop_bound_primitive_survives_a_later_run_call() -> None:
    """Proves the exact property asyncpg's Connection needs and did not have
    under `asyncio.run()`: something created while one coroutine is running
    (bound to that call's loop) must still be usable from a *later* `run()`
    call. An `asyncio.Lock` is a plain-stdlib stand-in for the same
    loop-binding behaviour asyncpg's Connection/Protocol has -- acquiring a
    lock created on a different, now-dead loop raises under `asyncio.run()`
    semantics; under a persistent per-process loop, both calls run on the
    same loop, so it just works.
    """
    holder: dict[str, asyncio.Lock] = {}

    async def create_lock() -> None:
        holder["lock"] = asyncio.Lock()

    async def use_lock() -> bool:
        async with holder["lock"]:
            return True

    async_runner.run(create_lock())
    # If this ran on a different loop than create_lock() did, acquiring the
    # lock would raise (a Lock's internal waiter Future is bound to the loop
    # that first touched it). It must not raise here.
    assert async_runner.run(use_lock()) is True


def test_close_runner_is_idempotent() -> None:
    async_runner.run(asyncio.sleep(0))
    async_runner.close_runner()
    async_runner.close_runner()  # must not raise the second time
    async_runner.close_runner()  # or the third


def test_close_runner_with_no_runner_ever_created_is_a_safe_no_op() -> None:
    # The autouse fixture already closed any runner; this process has none.
    async_runner.close_runner()


def test_a_new_run_after_close_creates_a_fresh_loop() -> None:
    async def capture_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    loop_before = async_runner.run(capture_loop())
    async_runner.close_runner()
    assert loop_before.is_closed()

    loop_after = async_runner.run(capture_loop())
    assert loop_after is not loop_before
    assert not loop_after.is_closed()


def test_get_session_factory_does_not_deadlock_on_first_call() -> None:
    """Regression for a real self-deadlock introduced and caught while
    building this fix: get_session_factory() acquires the module lock and
    then calls get_engine(), which acquires the *same* lock again on the
    same thread. A plain threading.Lock is not reentrant, so that nested
    acquisition hung forever -- found by the full test suite hanging on an
    unrelated FastAPI contract test, not by any test that exercises
    get_session_factory() directly. Guarded here so it can never silently
    regress back from RLock to a plain Lock.
    """
    import threading

    from app.db import session as db_session

    # Force the deadlock-prone path deterministically: the bug only
    # reproduces when _session_factory is None at call time, which is what
    # makes get_session_factory() actually call get_engine() while still
    # holding the lock.
    db_session._engine = None
    db_session._session_factory = None

    completed = threading.Event()

    def call_it() -> None:
        db_session.get_session_factory()
        completed.set()

    thread = threading.Thread(target=call_it, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert completed.is_set(), (
        "get_session_factory() did not return within 5s -- almost certainly "
        "the Lock/RLock self-deadlock regressing (see this test's docstring)."
    )


def test_close_runner_disposes_the_engine_on_the_same_loop() -> None:
    """The engine must be disposed *before* its owning loop closes, and on
    that same loop -- disposing an asyncio engine is itself a coroutine."""
    from app.db import session as db_session

    async def touch_engine() -> None:
        # Creating the engine is enough to prove dispose actually runs
        # against something real, without needing a live database --
        # create_async_engine() does not connect eagerly.
        db_session.get_engine()

    async_runner.run(touch_engine())
    assert db_session._engine is not None

    async_runner.close_runner()
    assert db_session._engine is None


def test_tasks_module_never_calls_asyncio_run_directly() -> None:
    """Source-level regression guard: the whole point of this module is that
    no Celery task wrapper creates its own event loop per call. If a future
    change reintroduces `asyncio.run(...)` in app/workers/tasks.py, this
    fails immediately rather than waiting for a production connection leak
    to surface it again."""
    import inspect

    from app.workers import tasks

    source = inspect.getsource(tasks)
    assert "asyncio.run(" not in source, (
        "app/workers/tasks.py calls asyncio.run() directly again -- every task "
        "wrapper must go through app.workers.async_runner.run() instead, or the "
        "process-cached AsyncEngine will be silently reused across a fresh loop "
        "per task, reproducing the RC1 connection leak."
    )


def test_close_runner_never_touches_a_different_processs_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the fork-safety half of the design: a child that inherited
    another process's runner reference must never call .close() on it --
    that would touch the parent's loop/selector fd from the wrong process.
    """
    async_runner.run(asyncio.sleep(0))
    real_runner = async_runner._runner
    assert real_runner is not None

    # Simulate "we are a forked child that inherited this module's globals,
    # but the runner on record actually belongs to some other process" --
    # -1 is never a real PID.
    monkeypatch.setattr(async_runner, "_runner_pid", -1)

    async_runner.close_runner()

    # The "other process's" runner must be untouched: still the same
    # object, still open -- close_runner() correctly refused to act on it.
    assert async_runner._runner is real_runner
    assert not real_runner.get_loop().is_closed()

    # monkeypatch reverts _runner_pid automatically at teardown; the
    # fixture's own close_runner() call then closes it for real, as this
    # process, with the correct PID restored.
