from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.providers.base import ProviderUsage


class CostCalculator:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

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


@lru_cache(maxsize=1)
def get_cost_calculator() -> CostCalculator:
    return CostCalculator(get_settings().pricing_config_path)
