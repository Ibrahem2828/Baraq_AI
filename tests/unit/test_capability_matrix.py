from __future__ import annotations

from app.models.enums import Provider
from app.providers.capabilities import Capability, supports


def test_openai_supports_every_capability() -> None:
    for capability in Capability:
        assert supports(Provider.OPENAI, capability)


def test_gemini_does_not_yet_support_transcription_or_moderation() -> None:
    assert supports(Provider.GEMINI, Capability.STRUCTURED_GENERATION)
    assert supports(Provider.GEMINI, Capability.EMBEDDINGS)
    assert not supports(Provider.GEMINI, Capability.TRANSCRIPTION)
    assert not supports(Provider.GEMINI, Capability.MODERATION)


def test_mock_and_replay_support_every_capability_for_test_determinism() -> None:
    for provider in (Provider.MOCK, Provider.REPLAY):
        for capability in Capability:
            assert supports(provider, capability)
