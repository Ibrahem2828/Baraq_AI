from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from app.core.config import get_settings

if TYPE_CHECKING:
    # Deferred: app.providers.base is only needed for this module's type
    # hints. Importing it eagerly closes a real cycle -- app.providers's
    # package __init__ imports the provider adapters, which import
    # get_cost_calculator from this module -- so it must stay import-time
    # inert here.
    from app.providers.base import ProviderUsage


class CostCalculator:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    @property
    def version(self) -> str:
        return str(self.data.get("pricing_version", "unknown"))

    def token_cost(self, model: str, usage: ProviderUsage) -> float:
        prices = self.data.get("models", {}).get(model, {})
        uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
        input_cost = uncached * float(prices.get("input_per_million", 0)) / 1_000_000
        cached_cost = (
            usage.cached_input_tokens
            * float(prices.get("cached_input_per_million", prices.get("input_per_million", 0)))
            / 1_000_000
        )
        output_cost = usage.output_tokens * float(prices.get("output_per_million", 0)) / 1_000_000
        return round(input_cost + cached_cost + output_cost, 8)

    def audio_cost(self, model: str, seconds: float) -> float:
        prices = self.data.get("models", {}).get(model, {})
        return round((seconds / 60.0) * float(prices.get("audio_per_minute", 0)), 8)

    def transcription_cost(self, model: str, *, seconds: float, usage: ProviderUsage) -> float:
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
        if strategy == "tokens" and (usage.input_tokens or usage.output_tokens):
            return self.token_cost(model, usage)
        return self.audio_cost(model, seconds)

    def embedding_cost(self, model: str, input_tokens: int) -> float:
        prices = self.data.get("models", {}).get(model, {})
        return round(input_tokens * float(prices.get("input_per_million", 0)) / 1_000_000, 8)


@lru_cache(maxsize=1)
def get_cost_calculator() -> CostCalculator:
    return CostCalculator(get_settings().pricing_config_path)
