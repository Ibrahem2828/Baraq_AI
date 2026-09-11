"""ProviderCandidate: the routing unit the spec requires.

Each candidate carries its own provider *and* model. A candidate is never
constructed by picking a model once and handing it to a different fallback
provider (spec section 5) -- that coupling is exactly what made the previous
``model_for_tier`` global lookup unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Provider, ProviderAccount, QualityTier
from app.providers.base import LLMProvider


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    provider: Provider
    account_id: ProviderAccount
    model: str
    task_type: str
    quality_tier: QualityTier
    timeout_seconds: int
    max_output_tokens: int
    budget_policy: str
    pricing_version: str
    priority: int
    reasoning_policy: str | None = None
    instance: LLMProvider | None = None
