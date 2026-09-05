from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.ai_job import ProviderUsageMonth
from app.models.enums import ProviderAccount
from app.providers.base import ProviderResult, TranscriptionResult


class ProviderBudgetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    @staticmethod
    def current_month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def budget_for(self, account: ProviderAccount) -> float:
        # Mock/Replay accounts fall through to 0.0, which `has_budget` below
        # treats as "no cap" -- consistent, since they never incur real cost.
        mapping = {
            ProviderAccount.PRIMARY: self.settings.openai_primary_monthly_budget_usd,
            ProviderAccount.SECONDARY: self.settings.openai_secondary_monthly_budget_usd,
            ProviderAccount.GEMINI_PRIMARY: self.settings.gemini_primary_monthly_budget_usd,
        }
        return mapping.get(account, 0.0)

    async def usage_for(self, account: ProviderAccount) -> ProviderUsageMonth | None:
        return cast(
            ProviderUsageMonth | None,
            await self.session.scalar(
                select(ProviderUsageMonth).where(
                    ProviderUsageMonth.provider_account == account,
                    ProviderUsageMonth.year_month == self.current_month(),
                )
            ),
        )

    async def has_budget(self, account: ProviderAccount) -> bool:
        budget = self.budget_for(account)
        if budget <= 0:
            return True
        usage = await self.usage_for(account)
        return usage is None or usage.estimated_cost_usd < budget

    async def record(self, result: ProviderResult | TranscriptionResult) -> None:
        usage = await self.usage_for(result.account)
        if usage is None:
            usage = ProviderUsageMonth(
                provider_account=result.account,
                year_month=self.current_month(),
            )
            self.session.add(usage)
            await self.session.flush()
        usage.request_count += 1
        usage.input_tokens += result.usage.input_tokens
        usage.output_tokens += result.usage.output_tokens
        usage.estimated_cost_usd = round(usage.estimated_cost_usd + result.estimated_cost_usd, 8)
