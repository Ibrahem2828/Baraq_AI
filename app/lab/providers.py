"""Provider-neutral factory for the standalone Lab.

Lab drives exactly one directly-configured :class:`LLMProvider`, selected by
the same ``PROVIDER_MODE`` switch the production router uses -- the same
Gemini/OpenAI/Mock/Replay classes, without the router's multi-account
failover or Redis-backed circuit breaker (Lab intentionally has no
Redis/Postgres dependency, spec section 30).
"""

from __future__ import annotations

from app.core.config import Settings
from app.models.enums import ProviderAccount
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.mock_provider import MockProvider
from app.providers.openai_provider import OpenAIProvider
from app.providers.replay_provider import ReplayProvider


def build_lab_provider(settings: Settings) -> LLMProvider:
    mode = settings.provider_mode
    if mode == "mock":
        return MockProvider()
    if mode == "replay":
        return ReplayProvider(fixtures_dir=settings.replay_fixtures_dir)

    if settings.openai_primary_enabled and settings.openai_primary_api_key.get_secret_value():
        return OpenAIProvider(
            account=ProviderAccount.PRIMARY,
            api_key=settings.openai_primary_api_key.get_secret_value(),
            project_id=settings.openai_primary_project_id,
            organization_id=settings.openai_primary_organization_id,
            base_url=settings.openai_primary_base_url,
            store_responses=settings.openai_store_responses,
            timeout_seconds=settings.openai_request_timeout_seconds,
        )
    if settings.gemini_primary_enabled and settings.gemini_primary_api_key.get_secret_value():
        return GeminiProvider(
            api_key=settings.gemini_primary_api_key.get_secret_value(),
            base_url=settings.gemini_primary_base_url,
            timeout_seconds=settings.gemini_request_timeout_seconds,
        )
    # PROVIDER_MODE=live with no configured credential: fail safe to Mock
    # rather than crash Lab startup. Every run stays clearly labeled
    # simulated (spec section 31) so this can never be mistaken for a real
    # provider measurement.
    return MockProvider()
