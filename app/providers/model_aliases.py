"""Resolve a provider-neutral quality tier to a real, provider-specific model
name. Model *names* live in Settings/env, never hard-coded in a pipeline
(spec section 5) -- this is the single place that does the lookup."""

from __future__ import annotations

from app.core.config import Settings
from app.models.enums import Provider, QualityTier

_SETTING_BY_TIER: dict[Provider, dict[QualityTier, str]] = {
    Provider.OPENAI: {
        QualityTier.FAST: "openai_model_fast",
        QualityTier.BALANCED: "openai_model_balanced",
        QualityTier.HIGH_QUALITY: "openai_model_high_quality",
    },
    Provider.GEMINI: {
        QualityTier.FAST: "gemini_model_fast",
        QualityTier.BALANCED: "gemini_model_balanced",
        QualityTier.HIGH_QUALITY: "gemini_model_high_quality",
    },
}


def model_for_tier(settings: Settings, provider: Provider, tier: str) -> str:
    try:
        quality_tier = QualityTier(tier)
    except ValueError:
        quality_tier = QualityTier.BALANCED
    mapping = _SETTING_BY_TIER.get(provider)
    if mapping is None:
        # Mock/Replay have no real model; the label is informational only
        # and is never sent to a network endpoint.
        return f"{provider.value}-{quality_tier.value}"
    attr = mapping.get(quality_tier, mapping[QualityTier.BALANCED])
    return str(getattr(settings, attr))


def embedding_model_for(settings: Settings, provider: Provider) -> str:
    if provider == Provider.GEMINI:
        return settings.gemini_embedding_model
    if provider == Provider.OPENAI:
        return settings.openai_embedding_model
    return f"{provider.value}-embedding"


def transcription_model_for(settings: Settings, provider: Provider) -> str:
    if provider == Provider.OPENAI:
        return settings.openai_transcription_model
    return f"{provider.value}-transcription"
