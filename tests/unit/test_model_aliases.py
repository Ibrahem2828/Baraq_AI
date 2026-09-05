from __future__ import annotations

from app.core.config import Settings
from app.models.enums import Provider
from app.providers.model_aliases import embedding_model_for, model_for_tier, transcription_model_for


def test_model_for_tier_resolves_per_provider_setting() -> None:
    settings = Settings()
    assert model_for_tier(settings, Provider.OPENAI, "fast") == settings.openai_model_fast
    assert model_for_tier(settings, Provider.GEMINI, "high_quality") == (
        settings.gemini_model_high_quality
    )


def test_model_for_tier_falls_back_to_balanced_for_an_unknown_tier() -> None:
    settings = Settings()
    assert model_for_tier(settings, Provider.OPENAI, "not_a_real_tier") == (
        settings.openai_model_balanced
    )


def test_model_for_tier_returns_an_informational_label_for_mock_and_replay() -> None:
    settings = Settings()
    assert model_for_tier(settings, Provider.MOCK, "fast") == "mock-fast"
    assert model_for_tier(settings, Provider.REPLAY, "balanced") == "replay-balanced"


def test_embedding_and_transcription_model_lookup() -> None:
    settings = Settings()
    assert embedding_model_for(settings, Provider.OPENAI) == settings.openai_embedding_model
    assert embedding_model_for(settings, Provider.GEMINI) == settings.gemini_embedding_model
    assert transcription_model_for(settings, Provider.OPENAI) == (
        settings.openai_transcription_model
    )
