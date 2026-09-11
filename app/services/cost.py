from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings

# Rough, standard English/Arabic-mixed-text heuristic (~4 characters per
# token) used only where a provider genuinely never reports exact usage
# (Gemini's embedContent response, see gemini_provider.embed) or ahead of a
# call, for a pre-flight budget reservation estimate -- never as a
# substitute for real usage when the provider does report it.
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE) if text else 0


class CostCalculator:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    @property
    def version(self) -> str:
        return str(self.data.get("pricing_version", "unknown"))

    def token_cost(
        self, model: str, *, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> float:
        prices = self.data.get("models", {}).get(model, {})
        uncached = max(0, input_tokens - cached_input_tokens)
        input_cost = uncached * float(prices.get("input_per_million", 0)) / 1_000_000
        cached_cost = (
            cached_input_tokens
            * float(prices.get("cached_input_per_million", prices.get("input_per_million", 0)))
            / 1_000_000
        )
        output_cost = output_tokens * float(prices.get("output_per_million", 0)) / 1_000_000
        return round(input_cost + cached_cost + output_cost, 8)

    def audio_cost(self, model: str, seconds: float) -> float:
        prices = self.data.get("models", {}).get(model, {})
        return round((seconds / 60.0) * float(prices.get("audio_per_minute", 0)), 8)

    def transcription_cost(
        self, model: str, *, seconds: float, input_tokens: int, output_tokens: int
    ) -> float:
        """Model-aware transcription billing.

        Not every OpenAI transcription model bills the same way: whisper-1 is
        genuinely duration-priced, but the gpt-4o-transcribe family is billed
        per audio/text token and merely has an OpenAI-published "estimated
        cost per minute" figure for convenience -- treating that estimate as
        the real price would silently over/under-bill relative to what OpenAI
        actually charges. `pricing_strategy` in pricing.yaml says which this
        model is; exact provider-reported usage is always preferred when the
        model is token-priced, falling back to the duration estimate only if
        the provider ever omits usage for such a model.
        """
        prices = self.data.get("models", {}).get(model, {})
        strategy = prices.get("pricing_strategy", "duration_minutes")
        if strategy == "tokens" and (input_tokens or output_tokens):
            return self.token_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)
        return self.audio_cost(model, seconds)

    def embedding_cost(self, model: str, input_tokens: int) -> float:
        prices = self.data.get("models", {}).get(model, {})
        return round(input_tokens * float(prices.get("input_per_million", 0)) / 1_000_000, 8)

    def max_generation_cost(
        self, model: str, *, estimated_input_tokens: int, max_output_tokens: int
    ) -> float:
        """Upper-bound cost for a not-yet-made generation call, for budget
        reservation. `max_output_tokens` is a real, provider-enforced ceiling
        (the call cannot emit more), so this is a true worst case for output;
        input is a char/4 estimate since exact input tokens aren't known
        before the call. Ignores any cache discount, which can only lower
        the eventual real cost, never raise it above this reservation."""
        return self.token_cost(
            model, input_tokens=estimated_input_tokens, output_tokens=max_output_tokens
        )

    def max_transcription_cost(self, model: str, *, max_seconds: float) -> float:
        """Upper-bound cost for a not-yet-made transcription call. Even for
        the token-priced gpt-4o-transcribe family, OpenAI's own published
        per-minute estimate (see pricing.yaml) is the only pre-call ceiling
        available -- exact token count depends on audio content the provider
        hasn't processed yet -- so it is used here as a reservation bound
        regardless of pricing_strategy, never as the committed price."""
        return self.audio_cost(model, max_seconds)


@lru_cache(maxsize=1)
def get_cost_calculator() -> CostCalculator:
    return CostCalculator(get_settings().pricing_config_path)
