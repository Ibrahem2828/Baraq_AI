from __future__ import annotations

from typing import cast

import pytest
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.errors import ProviderError
from app.models.enums import Provider, ProviderAccount
from app.providers.capabilities import Capability
from app.providers.mock_provider import MockProvider
from app.providers.router import ProviderRouter
from app.services.routing_config import get_routing_config


class FakeRedis:
    """Minimal in-memory stand-in for the subset of redis.asyncio.Redis that
    ProviderCircuitBreaker uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(
        self, key: str, value: object, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def incr(self, key: str) -> int:
        current = int(self.store.get(key, "0")) + 1
        self.store[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> None:
        return None


def _router(settings: Settings) -> ProviderRouter:
    return ProviderRouter(cast(Redis, FakeRedis()), settings=settings)


def _live_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "provider_mode": "live",
        "openai_primary_enabled": True,
        "openai_primary_api_key": "sk-test-primary",
        "gemini_primary_enabled": True,
        "gemini_primary_api_key": "test-gemini-key",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_each_candidate_pairs_its_own_provider_with_its_own_model() -> None:
    settings = _live_settings()
    router = _router(settings)
    routing = get_routing_config().get("fahes_generate_quiz")
    candidates = await router.ordered_candidates(routing, "routing-key")
    by_provider = {c.provider: c.model for c in candidates}
    assert by_provider[Provider.GEMINI] == settings.gemini_model_high_quality
    assert by_provider[Provider.OPENAI] == settings.openai_model_high_quality


@pytest.mark.asyncio
async def test_circuit_open_for_gemini_does_not_block_openai() -> None:
    settings = _live_settings()
    router = _router(settings)
    routing = get_routing_config().get("fahes_generate_quiz")
    for _ in range(settings.provider_circuit_failure_threshold):
        await router.circuit.record_failure(ProviderAccount.GEMINI_PRIMARY)
    candidates = await router.ordered_candidates(routing, "routing-key")
    assert {c.provider for c in candidates} == {Provider.OPENAI}


@pytest.mark.asyncio
async def test_circuit_open_for_openai_does_not_block_gemini() -> None:
    settings = _live_settings()
    router = _router(settings)
    routing = get_routing_config().get("fahes_generate_quiz")
    for _ in range(settings.provider_circuit_failure_threshold):
        await router.circuit.record_failure(ProviderAccount.PRIMARY)
    candidates = await router.ordered_candidates(routing, "routing-key")
    assert {c.provider for c in candidates} == {Provider.GEMINI}


@pytest.mark.asyncio
async def test_preferred_tier_overrides_the_routing_defaults_quality_tier() -> None:
    settings = _live_settings()
    router = _router(settings)
    routing = get_routing_config().get("khota_generate_plan")
    candidates = await router.ordered_candidates(routing, "routing-key", preferred_tier="fast")
    gemini_candidate = next(c for c in candidates if c.provider == Provider.GEMINI)
    assert gemini_candidate.model == settings.gemini_model_fast


@pytest.mark.asyncio
async def test_raises_when_no_provider_is_configured_in_live_mode() -> None:
    # Explicit empty credentials keep this hermetic even when a real
    # OPENAI_PRIMARY_API_KEY is present in the developer's local .env.
    router = _router(
        Settings(provider_mode="live", openai_primary_api_key="", gemini_primary_api_key="")
    )
    routing = get_routing_config().get("fahes_generate_quiz")
    with pytest.raises(ProviderError) as exc:
        await router.ordered_candidates(routing, "routing-key")
    assert exc.value.code == "no_provider_available"


@pytest.mark.asyncio
async def test_mock_mode_still_preserves_the_per_candidate_provider_field() -> None:
    router = _router(Settings(provider_mode="mock"))
    routing = get_routing_config().get("kholasa_generate_summary")
    candidates = await router.ordered_candidates(routing, "routing-key")
    assert {c.provider for c in candidates} == {Provider.GEMINI, Provider.OPENAI}
    assert all(isinstance(c.instance, MockProvider) for c in candidates)


@pytest.mark.asyncio
async def test_allow_fallback_false_returns_only_the_top_priority_candidate() -> None:
    settings = _live_settings()
    router = _router(settings)
    routing = get_routing_config().get("fahes_generate_quiz")
    candidates = await router.ordered_candidates(routing, "routing-key", allow_fallback=False)
    assert len(candidates) == 1
    assert candidates[0].provider == Provider.GEMINI  # priority 10, the routing config default


@pytest.mark.asyncio
async def test_candidates_for_capability_only_returns_supporting_providers() -> None:
    settings = _live_settings()
    router = _router(settings)
    candidates = await router.candidates_for_capability(Capability.TRANSCRIPTION, "routing-key")
    assert {c.provider for c in candidates} == {Provider.OPENAI}
