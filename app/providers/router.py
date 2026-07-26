from __future__ import annotations

import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.errors import ProviderError
from app.models.enums import ProviderAccount
from app.providers.base import LLMProvider
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.providers.openai_provider import OpenAIProvider


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    account: ProviderAccount
    provider: LLMProvider
    weight: int


class ProviderRouter:
    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.circuit = ProviderCircuitBreaker(redis)
        self.candidates = self._build_candidates()

    def _build_candidates(self) -> list[ProviderCandidate]:
        candidates: list[ProviderCandidate] = []
        primary_key = self.settings.openai_primary_api_key.get_secret_value()
        if self.settings.openai_primary_enabled and primary_key:
            candidates.append(
                ProviderCandidate(
                    ProviderAccount.PRIMARY,
                    OpenAIProvider(
                        account=ProviderAccount.PRIMARY,
                        api_key=primary_key,
                        project_id=self.settings.openai_primary_project_id,
                        organization_id=self.settings.openai_primary_organization_id,
                        base_url=self.settings.openai_primary_base_url,
                        store_responses=self.settings.openai_store_responses,
                        timeout_seconds=self.settings.openai_request_timeout_seconds,
                    ),
                    self.settings.openai_primary_weight,
                )
            )
        secondary_key = self.settings.openai_secondary_api_key.get_secret_value()
        if self.settings.openai_secondary_enabled and secondary_key:
            candidates.append(
                ProviderCandidate(
                    ProviderAccount.SECONDARY,
                    OpenAIProvider(
                        account=ProviderAccount.SECONDARY,
                        api_key=secondary_key,
                        project_id=self.settings.openai_secondary_project_id,
                        organization_id=self.settings.openai_secondary_organization_id,
                        base_url=self.settings.openai_secondary_base_url,
                        store_responses=self.settings.openai_store_responses,
                        timeout_seconds=self.settings.openai_request_timeout_seconds,
                    ),
                    self.settings.openai_secondary_weight,
                )
            )
        return candidates

    async def ordered_candidates(self, routing_key: str) -> list[ProviderCandidate]:
        available = [c for c in self.candidates if not await self.circuit.is_open(c.account)]
        if not available:
            raise ProviderError(
                "No AI provider account is currently available",
                code="no_provider_available",
                retryable=True,
            )
        if self.settings.provider_routing_policy == "failover" or len(available) == 1:
            return sorted(available, key=lambda c: c.account != ProviderAccount.PRIMARY)

        digest = int(hashlib.sha256(routing_key.encode()).hexdigest()[:8], 16)
        total = sum(max(0, item.weight) for item in available)
        if total <= 0:
            return available
        slot = digest % total
        cursor = 0
        selected = available[0]
        for item in available:
            cursor += item.weight
            if slot < cursor:
                selected = item
                break
        return [selected, *[item for item in available if item.account != selected.account]]

    def model_for_tier(self, tier: str) -> str:
        mapping = {
            "high_quality": self.settings.openai_model_high_quality,
            "balanced": self.settings.openai_model_balanced,
            "fast": self.settings.openai_model_fast,
        }
        return mapping.get(tier, self.settings.openai_model_balanced)
