from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.models.enums import Provider, ProviderAccount


@dataclass(slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    audio_seconds: float = 0.0


@dataclass(slots=True)
class ProviderResult:
    data: dict[str, Any]
    account: ProviderAccount
    model: str
    response_id: str | None
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    segments: list[dict[str, Any]]
    account: ProviderAccount
    model: str
    response_id: str | None
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    latency_ms: int = 0
    estimated_cost_usd: float = 0.0


class LLMProvider(ABC):
    account: ProviderAccount
    provider_family: Provider

    @abstractmethod
    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        output_model: type[BaseModel],
        schema_name: str,
        max_output_tokens: int,
        reasoning_effort: str | None,
        metadata: dict[str, str],
    ) -> ProviderResult: ...

    @abstractmethod
    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def transcribe(
        self,
        *,
        model: str,
        filename: str,
        content: bytes,
        language: str | None,
        prompt: str | None,
        diarize: bool,
    ) -> TranscriptionResult: ...

    @abstractmethod
    async def moderate(self, *, model: str, inputs: list[str]) -> list[dict[str, Any]]: ...
