from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.errors import ProviderError
from app.models.enums import Provider, ProviderAccount, QualityTier
from app.providers.base import LLMProvider
from app.providers.candidate import ProviderCandidate
from app.providers.capabilities import Capability, supports
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.providers.gemini_provider import GeminiProvider
from app.providers.mock_provider import MockProvider
from app.providers.model_aliases import model_for_tier
from app.providers.openai_provider import OpenAIProvider
from app.providers.replay_provider import ReplayProvider
from app.services.cost import get_cost_calculator
from app.services.routing_config import TaskRouting

_DEFAULT_CAPABILITY_PROVIDERS: dict[Capability, list[Provider]] = {
    Capability.EMBEDDINGS: [Provider.OPENAI, Provider.GEMINI],
    Capability.TRANSCRIPTION: [Provider.OPENAI],
    Capability.MODERATION: [Provider.OPENAI],
}


class ProviderRouter:
    """Builds provider instances once per ``PROVIDER_MODE`` and resolves each
    task's routing candidates into concrete, provider-owned model calls.

    A single explicit switch (``PROVIDER_MODE=mock|replay|live``) decides the
    whole provider layer -- never scattered ``if debug`` branches (spec
    section 6). In ``live`` mode, Gemini and OpenAI are independent: a
    circuit opened for one account never blocks the other (spec section 5).
    """

    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.circuit = ProviderCircuitBreaker(redis)
        self._instances = self._build_instances()

    def _build_instances(self) -> dict[ProviderAccount, LLMProvider]:
        mode = self.settings.provider_mode
        all_accounts = (
            ProviderAccount.PRIMARY,
            ProviderAccount.SECONDARY,
            ProviderAccount.GEMINI_PRIMARY,
        )
        if mode == "mock":
            return dict.fromkeys(all_accounts, MockProvider())
        if mode == "replay":
            shared_replay = ReplayProvider(fixtures_dir=self.settings.replay_fixtures_dir)
            return dict.fromkeys(all_accounts, shared_replay)

        instances: dict[ProviderAccount, LLMProvider] = {}
        primary_key = self.settings.openai_primary_api_key.get_secret_value()
        if self.settings.openai_primary_enabled and primary_key:
            instances[ProviderAccount.PRIMARY] = OpenAIProvider(
                account=ProviderAccount.PRIMARY,
                api_key=primary_key,
                project_id=self.settings.openai_primary_project_id,
                organization_id=self.settings.openai_primary_organization_id,
                base_url=self.settings.openai_primary_base_url,
                store_responses=self.settings.openai_store_responses,
                timeout_seconds=self.settings.openai_request_timeout_seconds,
            )
        secondary_key = self.settings.openai_secondary_api_key.get_secret_value()
        if self.settings.openai_secondary_enabled and secondary_key:
            instances[ProviderAccount.SECONDARY] = OpenAIProvider(
                account=ProviderAccount.SECONDARY,
                api_key=secondary_key,
                project_id=self.settings.openai_secondary_project_id,
                organization_id=self.settings.openai_secondary_organization_id,
                base_url=self.settings.openai_secondary_base_url,
                store_responses=self.settings.openai_store_responses,
                timeout_seconds=self.settings.openai_request_timeout_seconds,
            )
        gemini_key = self.settings.gemini_primary_api_key.get_secret_value()
        if self.settings.gemini_primary_enabled and gemini_key:
            instances[ProviderAccount.GEMINI_PRIMARY] = GeminiProvider(
                api_key=gemini_key,
                base_url=self.settings.gemini_primary_base_url,
                timeout_seconds=self.settings.gemini_request_timeout_seconds,
            )
        return instances

    def _accounts_for(self, provider: Provider) -> list[tuple[ProviderAccount, int]]:
        if provider == Provider.OPENAI:
            pairs = []
            if ProviderAccount.PRIMARY in self._instances:
                pairs.append((ProviderAccount.PRIMARY, self.settings.openai_primary_weight))
            if ProviderAccount.SECONDARY in self._instances:
                pairs.append((ProviderAccount.SECONDARY, self.settings.openai_secondary_weight))
            return pairs
        if provider == Provider.GEMINI:
            if ProviderAccount.GEMINI_PRIMARY in self._instances:
                return [(ProviderAccount.GEMINI_PRIMARY, 100)]
            return []
        account = ProviderAccount.MOCK if provider == Provider.MOCK else ProviderAccount.REPLAY
        return [(account, 100)] if account in self._instances else []

    def _select_account(
        self, accounts: list[tuple[ProviderAccount, int]], routing_key: str
    ) -> ProviderAccount:
        if len(accounts) == 1 or self.settings.provider_routing_policy == "failover":
            return accounts[0][0]
        digest = int(hashlib.sha256(routing_key.encode()).hexdigest()[:8], 16)
        total = sum(max(0, weight) for _, weight in accounts)
        if total <= 0:
            return accounts[0][0]
        slot = digest % total
        cursor = 0
        for account, weight in accounts:
            cursor += weight
            if slot < cursor:
                return account
        return accounts[-1][0]

    async def ordered_candidates(
        self,
        routing: TaskRouting,
        routing_key: str,
        *,
        allow_fallback: bool = True,
        preferred_tier: str | None = None,
    ) -> list[ProviderCandidate]:
        pricing_version = get_cost_calculator().version
        override_tier: QualityTier | None = None
        if preferred_tier:
            try:
                override_tier = QualityTier(preferred_tier)
            except ValueError:
                override_tier = None
        candidates: list[ProviderCandidate] = []
        for spec in routing.candidates:
            if not supports(spec.provider, Capability.STRUCTURED_GENERATION):
                continue
            accounts = self._accounts_for(spec.provider)
            live_accounts = [
                pair for pair in accounts if not await self.circuit.is_open(pair[0])
            ]
            if not live_accounts:
                continue
            account = self._select_account(live_accounts, routing_key)
            quality_tier = override_tier or spec.quality_tier
            candidates.append(
                ProviderCandidate(
                    provider=spec.provider,
                    account_id=account,
                    model=model_for_tier(self.settings, spec.provider, quality_tier.value),
                    task_type=routing.task_type,
                    quality_tier=quality_tier,
                    timeout_seconds=routing.timeout_seconds,
                    max_output_tokens=routing.max_output_tokens,
                    budget_policy=f"monthly:{account.value}",
                    pricing_version=pricing_version,
                    priority=spec.priority,
                    reasoning_policy=routing.reasoning_effort,
                    instance=self._instances[account],
                )
            )
        if not candidates:
            raise ProviderError(
                "No AI provider account is currently available for this task",
                code="no_provider_available",
                retryable=True,
            )
        return candidates if allow_fallback else candidates[:1]

    async def candidates_for_capability(
        self,
        capability: Capability,
        routing_key: str,
        *,
        allow_fallback: bool = True,
    ) -> list[ProviderCandidate]:
        """Route embeddings/transcription/moderation, which have no per-task
        entry in ``model_routing.yaml`` -- they are capabilities, not one of
        the five canonical task types."""
        pricing_version = get_cost_calculator().version
        candidates: list[ProviderCandidate] = []
        for provider in _DEFAULT_CAPABILITY_PROVIDERS.get(capability, []):
            if not supports(provider, capability):
                continue
            accounts = self._accounts_for(provider)
            live_accounts = [pair for pair in accounts if not await self.circuit.is_open(pair[0])]
            if not live_accounts:
                continue
            account = self._select_account(live_accounts, routing_key)
            candidates.append(
                ProviderCandidate(
                    provider=provider,
                    account_id=account,
                    model="",
                    task_type=capability.value,
                    quality_tier=QualityTier.BALANCED,
                    timeout_seconds=self.settings.openai_request_timeout_seconds,
                    max_output_tokens=0,
                    budget_policy=f"monthly:{account.value}",
                    pricing_version=pricing_version,
                    priority=0,
                    instance=self._instances[account],
                )
            )
        if not candidates:
            raise ProviderError(
                f"No AI provider account is currently available for {capability.value}",
                code="no_provider_available",
                retryable=True,
            )
        return candidates if allow_fallback else candidates[:1]
