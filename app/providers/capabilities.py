"""Capability matrix: prevents the router from picking a model/provider that
does not support the requested operation (spec section 5)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import Provider


class Capability(StrEnum):
    STRUCTURED_GENERATION = "structured_generation"
    EMBEDDINGS = "embeddings"
    TRANSCRIPTION = "transcription"
    MODERATION = "moderation"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider: Provider
    supports: frozenset[Capability]

    def can(self, capability: Capability) -> bool:
        return capability in self.supports


# Gemini's Files/Audio understanding API can transcribe, but Baraq does not
# route Sada through it today (Sada uses Local Whisper in Lab and the
# approved production STT provider in service mode) -- transcription support
# is declared false here until that adapter path is implemented and tested.
_MATRIX: dict[Provider, ProviderCapabilities] = {
    Provider.OPENAI: ProviderCapabilities(
        provider=Provider.OPENAI,
        supports=frozenset(
            {
                Capability.STRUCTURED_GENERATION,
                Capability.EMBEDDINGS,
                Capability.TRANSCRIPTION,
                Capability.MODERATION,
            }
        ),
    ),
    Provider.GEMINI: ProviderCapabilities(
        provider=Provider.GEMINI,
        supports=frozenset({Capability.STRUCTURED_GENERATION, Capability.EMBEDDINGS}),
    ),
    Provider.MOCK: ProviderCapabilities(
        provider=Provider.MOCK,
        supports=frozenset(
            {
                Capability.STRUCTURED_GENERATION,
                Capability.EMBEDDINGS,
                Capability.TRANSCRIPTION,
                Capability.MODERATION,
            }
        ),
    ),
    Provider.REPLAY: ProviderCapabilities(
        provider=Provider.REPLAY,
        supports=frozenset(
            {
                Capability.STRUCTURED_GENERATION,
                Capability.EMBEDDINGS,
                Capability.TRANSCRIPTION,
                Capability.MODERATION,
            }
        ),
    ),
}


def capabilities_for(provider: Provider) -> ProviderCapabilities:
    return _MATRIX[provider]


def supports(provider: Provider, capability: Capability) -> bool:
    return capabilities_for(provider).can(capability)
