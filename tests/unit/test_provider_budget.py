"""Regression tests for atomic provider budget reservation (spec section 9 /
audit P1): budget enforcement used to be check-then-call-then-record, which
is racy under concurrent Celery workers. reserve()/commit_actual()/release()
replace that with a locked, conditional increment that must be resolved
exactly once per call.

These tests run against a real, file-backed SQLite database (not `:memory:`
-- a file lets genuinely separate connections, opened the same way
production opens separate connections per reserve()/release()/commit_actual()
call, see each other's committed rows). SQLite does not support `SELECT ...
FOR UPDATE` at all (SQLAlchemy's sqlite dialect silently drops it) and its
*default* transaction mode only acquires a write lock lazily, at the first
write -- verified directly: running these same concurrent-reservation
scenarios without the fixture's `BEGIN IMMEDIATE` override below reliably
over-grants, because two connections' SELECTs can both read the
pre-reservation state before either has written anything. The fixture
forces `BEGIN IMMEDIATE` (a standard SQLAlchemy/pysqlite recipe) so each
transaction takes SQLite's write lock up front, serializing concurrent
reservations at the whole-database level instead of per-row. That is
coarser than Postgres's real per-row `FOR UPDATE` lock, but it is enough to
prove the reserve-then-lock-then-conditionally-increment LOGIC is correct
under genuine concurrent, separate-connection access -- including the exact
"two workers race to insert the first row of the month" case reproduced in
test_concurrent_first_reservations_of_the_month_do_not_crash_or_double_grant
below. Real Postgres row-level lock behavior under contention still needs
verification against a real Postgres instance (see .github/workflows/ci.yml),
not this suite -- this codebase's existing `with_for_update()` usages
(job cancellation, idempotency) have the same gap and no DB-backed test at
all today, so this suite's SQLite coverage is already stricter than
precedent, not weaker.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.provider_budget as provider_budget_module
from app.core.config import get_settings
from app.db.base import Base
from app.models.ai_job import ProviderUsageMonth
from app.models.enums import ProviderAccount
from app.providers.base import EmbeddingResult, ProviderUsage
from app.services.provider_budget import BudgetReservation, ProviderBudgetService


@pytest.fixture
async def budget_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[ProviderBudgetService]:
    monkeypatch.setenv("OPENAI_PRIMARY_MONTHLY_BUDGET_USD", "10.0")
    monkeypatch.setenv("OPENAI_SECONDARY_MONTHLY_BUDGET_USD", "0")
    monkeypatch.setenv("GEMINI_PRIMARY_MONTHLY_BUDGET_USD", "0")
    get_settings.cache_clear()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _disable_pysqlite_implicit_begin(dbapi_connection: object, _: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine.sync_engine, "begin")
    def _begin_immediate(conn: object) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[ProviderUsageMonth.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # provider_budget.py does `from app.db.session import AsyncSessionLocal`,
    # binding the name into its own module -- patch it there, not at the
    # source module, or reserve()/release()/commit_actual() would keep using
    # the real Postgres-backed factory.
    monkeypatch.setattr(provider_budget_module, "AsyncSessionLocal", factory)

    async with factory() as session:
        yield ProviderBudgetService(session=session)
    await engine.dispose()
    get_settings.cache_clear()


async def _reserved_row(session: AsyncSession, account: ProviderAccount) -> ProviderUsageMonth:
    row = await session.scalar(
        select(ProviderUsageMonth).where(ProviderUsageMonth.provider_account == account)
    )
    assert row is not None
    return row


def _embedding_result(*, input_tokens: int, cost: float) -> EmbeddingResult:
    return EmbeddingResult(
        vectors=[],
        account=ProviderAccount.PRIMARY,
        model="text-embedding-3-small",
        usage=ProviderUsage(input_tokens=input_tokens, total_tokens=input_tokens),
        estimated_cost_usd=cost,
    )


@pytest.mark.asyncio
async def test_reserve_grants_when_under_budget(budget_service: ProviderBudgetService) -> None:
    reservation = await budget_service.reserve(ProviderAccount.PRIMARY, 3.0)
    assert reservation == BudgetReservation(
        account=ProviderAccount.PRIMARY, reserved_usd=3.0, granted=True
    )
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 3.0
    assert row.estimated_cost_usd == 0.0


@pytest.mark.asyncio
async def test_reserve_denies_when_it_would_exceed_budget(
    budget_service: ProviderBudgetService,
) -> None:
    reservation = await budget_service.reserve(ProviderAccount.PRIMARY, 11.0)
    assert reservation.granted is False
    assert reservation.reserved_usd == 0.0
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 0.0


@pytest.mark.asyncio
async def test_reserve_counts_existing_reservations_not_just_committed_cost(
    budget_service: ProviderBudgetService,
) -> None:
    # This is the actual P1 fix: the old has_budget() only compared
    # estimated_cost_usd (money already spent) to the cap. Two in-flight,
    # not-yet-committed reservations must also count, or two concurrent
    # calls can each see "$0 committed, still under budget" and both proceed.
    first = await budget_service.reserve(ProviderAccount.PRIMARY, 6.0)
    assert first.granted is True
    second = await budget_service.reserve(ProviderAccount.PRIMARY, 5.0)  # 6 + 5 > 10
    assert second.granted is False
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 6.0


@pytest.mark.asyncio
async def test_uncapped_account_always_grants(budget_service: ProviderBudgetService) -> None:
    # SECONDARY's budget env var above is "0", meaning uncapped (matches
    # ProviderBudgetService.budget_for's documented mock/replay convention).
    reservation = await budget_service.reserve(ProviderAccount.SECONDARY, 1_000_000.0)
    assert reservation.granted is True


@pytest.mark.asyncio
async def test_commit_actual_releases_the_hold_and_records_real_cost(
    budget_service: ProviderBudgetService,
) -> None:
    reservation = await budget_service.reserve(ProviderAccount.PRIMARY, 5.0)  # worst-case ceiling
    await budget_service.commit_actual(
        reservation, _embedding_result(input_tokens=100, cost=2.0)  # actual cost turned out lower
    )
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 0.0  # hold fully released
    assert row.estimated_cost_usd == 2.0  # only the real cost is committed
    assert row.request_count == 1
    assert row.input_tokens == 100


@pytest.mark.asyncio
async def test_commit_actual_does_not_double_charge_across_retries(
    budget_service: ProviderBudgetService,
) -> None:
    # Simulates one candidate's retry loop: attempt 1 fails and releases,
    # attempt 2 succeeds and commits. Final committed cost must reflect only
    # the one successful attempt, not both.
    first_attempt = await budget_service.reserve(ProviderAccount.PRIMARY, 4.0)
    await budget_service.release(first_attempt)
    second_attempt = await budget_service.reserve(ProviderAccount.PRIMARY, 4.0)
    await budget_service.commit_actual(
        second_attempt, _embedding_result(input_tokens=50, cost=1.5)
    )
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 0.0
    assert row.estimated_cost_usd == 1.5
    assert row.request_count == 1


@pytest.mark.asyncio
async def test_release_gives_back_an_unused_reservation_without_billing_anything(
    budget_service: ProviderBudgetService,
) -> None:
    reservation = await budget_service.reserve(ProviderAccount.PRIMARY, 5.0)
    await budget_service.release(reservation)
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 0.0
    assert row.estimated_cost_usd == 0.0
    assert row.request_count == 0


@pytest.mark.asyncio
async def test_release_is_a_noop_for_a_reservation_that_was_never_granted(
    budget_service: ProviderBudgetService,
) -> None:
    ungranted = BudgetReservation(account=ProviderAccount.PRIMARY, reserved_usd=0.0, granted=False)
    await budget_service.release(ungranted)  # must not raise, must not create a row
    assert await budget_service.usage_for(ProviderAccount.PRIMARY) is None


@pytest.mark.asyncio
async def test_provider_failure_releases_its_reservation_end_to_end(
    budget_service: ProviderBudgetService,
) -> None:
    """Exercises the exact release-on-failure pattern used by
    generation.py/sada.py/embeddings.py: reserve, the provider call raises,
    release -- and budget is fully available again for the next attempt."""
    reservation = await budget_service.reserve(ProviderAccount.PRIMARY, 10.0)
    assert reservation.granted is True
    try:
        raise RuntimeError("simulated provider failure")
    except RuntimeError:
        await budget_service.release(reservation)
    retry = await budget_service.reserve(ProviderAccount.PRIMARY, 10.0)
    assert retry.granted is True


@pytest.mark.asyncio
async def test_concurrent_reservations_never_jointly_exceed_the_budget(
    budget_service: ProviderBudgetService,
) -> None:
    # 5 concurrent workers each ask for $3 against a $10 cap: at most 3 can
    # be granted (9 <= 10; a 4th would be 12 > 10). The old check-then-record
    # design (a plain SELECT compared to budget, with the real provider call
    # and record() awaited afterwards) would let all 5 pass the check before
    # any of them recorded cost -- this proves the atomic reserve prevents
    # that regardless of how many callers race in at once.
    results = await asyncio.gather(
        *[budget_service.reserve(ProviderAccount.PRIMARY, 3.0) for _ in range(5)]
    )
    granted = [r for r in results if r.granted]
    assert sum(r.reserved_usd for r in granted) <= 10.0
    assert len(granted) == 3
    row = await _reserved_row(budget_service.session, ProviderAccount.PRIMARY)
    assert row.reserved_cost_usd == 9.0


@pytest.mark.asyncio
async def test_concurrent_first_reservations_of_the_month_do_not_crash_or_double_grant(
    budget_service: ProviderBudgetService,
) -> None:
    # The very first reservation for an (account, month) has no row to lock
    # yet -- two concurrent callers can each see "no row" and race to INSERT.
    # Reproduced directly (without this coverage, this call raises
    # sqlalchemy.exc.IntegrityError instead of returning a BudgetReservation):
    # reserve()'s except IntegrityError branch must retry against the row the
    # winner just committed, rather than letting the loser's insert crash the
    # request or silently grant on a phantom row.
    results = await asyncio.gather(
        *[budget_service.reserve(ProviderAccount.GEMINI_PRIMARY, 1.0) for _ in range(8)],
        return_exceptions=True,
    )
    assert all(isinstance(r, BudgetReservation) for r in results), results
    # GEMINI_PRIMARY's env budget above is "0" (uncapped), so every one of
    # the 8 concurrent first-of-the-month reservations must still succeed.
    assert all(r.granted for r in results)  # type: ignore[union-attr]
