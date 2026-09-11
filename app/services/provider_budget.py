from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.ai_job import ProviderUsageMonth
from app.models.enums import ProviderAccount
from app.providers.base import EmbeddingResult, ProviderResult, TranscriptionResult


@dataclass(slots=True, frozen=True)
class BudgetReservation:
    """A provisional hold against a monthly budget, taken before a paid call
    is made so concurrent workers can't each pass a stale "still under
    budget" check and jointly overspend. Every reservation -- granted or not
    -- must be resolved exactly once, by either `commit_actual()` (the call
    happened -- convert the hold into the real recorded cost) or `release()`
    (the call never happened, or failed, or a different candidate was used
    instead)."""

    account: ProviderAccount
    reserved_usd: float
    granted: bool


class ProviderBudgetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    @staticmethod
    def current_month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def budget_for(self, account: ProviderAccount) -> float:
        # Mock/Replay accounts fall through to 0.0, which reserve() below
        # treats as "no cap" -- consistent, since they never incur real cost.
        mapping = {
            ProviderAccount.PRIMARY: self.settings.openai_primary_monthly_budget_usd,
            ProviderAccount.SECONDARY: self.settings.openai_secondary_monthly_budget_usd,
            ProviderAccount.GEMINI_PRIMARY: self.settings.gemini_primary_monthly_budget_usd,
        }
        return mapping.get(account, 0.0)

    async def usage_for(self, account: ProviderAccount) -> ProviderUsageMonth | None:
        """Read-only snapshot using the caller's own session (e.g. for
        reporting within an already-open unit of work). The mutating
        reserve/commit_actual/release methods below deliberately do NOT use
        `self.session` -- see their docstrings."""
        return cast(
            ProviderUsageMonth | None,
            await self.session.scalar(
                select(ProviderUsageMonth).where(
                    ProviderUsageMonth.provider_account == account,
                    ProviderUsageMonth.year_month == self.current_month(),
                )
            ),
        )

    @staticmethod
    async def _locked_usage_row(
        session: AsyncSession, account: ProviderAccount
    ) -> ProviderUsageMonth:
        usage = await session.scalar(
            select(ProviderUsageMonth)
            .where(
                ProviderUsageMonth.provider_account == account,
                ProviderUsageMonth.year_month == ProviderBudgetService.current_month(),
            )
            .with_for_update()
        )
        if usage is None:
            usage = ProviderUsageMonth(
                provider_account=account, year_month=ProviderBudgetService.current_month()
            )
            session.add(usage)
            await session.flush()
        return usage

    async def _reserve_once(
        self, account: ProviderAccount, max_cost_usd: float
    ) -> BudgetReservation:
        budget = self.budget_for(account)
        async with AsyncSessionLocal() as session:
            usage = await self._locked_usage_row(session, account)
            committed = usage.estimated_cost_usd + usage.reserved_cost_usd
            if budget > 0 and committed + max_cost_usd > budget:
                await session.commit()
                return BudgetReservation(account=account, reserved_usd=0.0, granted=False)
            usage.reserved_cost_usd = round(usage.reserved_cost_usd + max_cost_usd, 8)
            await session.commit()
        return BudgetReservation(account=account, reserved_usd=max_cost_usd, granted=True)

    async def reserve(self, account: ProviderAccount, max_cost_usd: float) -> BudgetReservation:
        """Atomically hold up to `max_cost_usd` against this account's
        monthly budget before a paid call is made.

        Deliberately uses its own short-lived session/transaction (mirroring
        `OutboxDispatcher`'s pattern in app/services/outbox_dispatcher.py)
        rather than the caller's long-lived, per-job session: the row lock
        this takes (SELECT ... FOR UPDATE) must be held only for the instant
        it takes to check-and-increment, not for however long the rest of
        the pipeline's larger transaction stays open -- otherwise a slow
        job would serialize every other concurrent call against the same
        provider account behind its lock for its whole duration.
        """
        try:
            return await self._reserve_once(account, max_cost_usd)
        except IntegrityError:
            # First reservation of the month for this account: two workers
            # raced to insert the usage row, and the loser's FOR UPDATE query
            # found no row to lock. The winner's insert is now committed;
            # retry once, and this time FOR UPDATE finds and locks it.
            return await self._reserve_once(account, max_cost_usd)

    async def commit_actual(
        self,
        reservation: BudgetReservation,
        result: ProviderResult | TranscriptionResult | EmbeddingResult,
    ) -> None:
        """Reconcile a granted reservation with the real cost of a call that
        succeeded: release the hold and record the real usage/cost in the
        same locked row update, so nothing is ever double-counted. Uses its
        own short-lived session, same reasoning as `reserve()`."""
        async with AsyncSessionLocal() as session:
            usage = await self._locked_usage_row(session, reservation.account)
            usage.reserved_cost_usd = round(
                max(0.0, usage.reserved_cost_usd - reservation.reserved_usd), 8
            )
            usage.request_count += 1
            usage.input_tokens += result.usage.input_tokens
            usage.output_tokens += result.usage.output_tokens
            usage.estimated_cost_usd = round(
                usage.estimated_cost_usd + result.estimated_cost_usd, 8
            )
            await session.commit()

    async def release(self, reservation: BudgetReservation) -> None:
        """Give back a reservation that was never spent: the call failed,
        raised before completion, or a different candidate succeeded
        instead. A no-op for a reservation that was never granted. Uses its
        own short-lived session, same reasoning as `reserve()`."""
        if not reservation.granted or reservation.reserved_usd <= 0:
            return
        async with AsyncSessionLocal() as session:
            usage = await self._locked_usage_row(session, reservation.account)
            usage.reserved_cost_usd = round(
                max(0.0, usage.reserved_cost_usd - reservation.reserved_usd), 8
            )
            await session.commit()
