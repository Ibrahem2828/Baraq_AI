"""Regression tests for the P0 gap where paid transcription and embedding
calls silently recorded $0 cost (spec section 8/audit P0). Each provider must
compute a real, non-zero `estimated_cost_usd` from the actual usage it gets
back from the SDK, using the same `config/pricing.yaml` prices the rest of
the budget system relies on."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.enums import ProviderAccount
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider
from app.services.cost import get_cost_calculator


def _openai_provider() -> OpenAIProvider:
    return OpenAIProvider(
        account=ProviderAccount.PRIMARY,
        api_key="test-key",
        project_id=None,
        organization_id=None,
        base_url="https://api.openai.com/v1",
        store_responses=False,
        timeout_seconds=30,
    )


def _gemini_provider() -> GeminiProvider:
    return GeminiProvider(
        api_key="test-key", base_url="https://example.invalid", timeout_seconds=30
    )


@pytest.mark.asyncio
async def test_openai_transcribe_computes_real_audio_cost_not_zero() -> None:
    provider = _openai_provider()
    fake_response = SimpleNamespace(
        text="hello world",
        segments=[{"start": 0.0, "end": 30.0, "text": "hello world"}],
        usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0),
        duration=30.0,
        id="resp-1",
    )
    provider.client.audio.transcriptions.create = AsyncMock(return_value=fake_response)
    result = await provider.transcribe(
        model="gpt-4o-mini-transcribe",
        filename="lecture.mp3",
        content=b"fake-audio-bytes",
        language="ar",
        prompt=None,
        diarize=False,
    )
    # 30s = 0.5 minute * 0.003 USD/minute (config/pricing.yaml) = 0.0015.
    assert result.estimated_cost_usd == 0.0015
    assert result.usage.audio_seconds == 30.0


@pytest.mark.asyncio
async def test_openai_transcribe_bills_by_token_usage_when_the_api_reports_it() -> None:
    # gpt-4o-transcribe/-mini/-diarize are billed per token by OpenAI, not per
    # minute -- when the API reports real usage (the normal case), that must
    # win over the per-minute estimate, even though duration is also present.
    provider = _openai_provider()
    fake_response = SimpleNamespace(
        text="hello world",
        segments=[{"start": 0.0, "end": 30.0, "text": "hello world"}],
        usage=SimpleNamespace(
            input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000
        ),
        duration=30.0,
        id="resp-1",
    )
    provider.client.audio.transcriptions.create = AsyncMock(return_value=fake_response)
    result = await provider.transcribe(
        model="gpt-4o-mini-transcribe",
        filename="lecture.mp3",
        content=b"fake-audio-bytes",
        language="ar",
        prompt=None,
        diarize=False,
    )
    # 1M input * 1.25/M + 1M output * 5.00/M (config/pricing.yaml) = 6.25.
    # The duration-only estimate for 30s would be 0.0015 -- token pricing
    # must win, not be silently overridden by the cheaper per-minute figure.
    assert result.estimated_cost_usd == 6.25


@pytest.mark.asyncio
async def test_openai_transcribe_falls_back_to_segment_end_when_duration_missing() -> None:
    provider = _openai_provider()
    fake_response = SimpleNamespace(
        text="hello",
        segments=[{"start": 0.0, "end": 60.0, "text": "hello"}],
        usage=SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0),
        id="resp-2",
    )
    provider.client.audio.transcriptions.create = AsyncMock(return_value=fake_response)
    result = await provider.transcribe(
        model="gpt-4o-mini-transcribe",
        filename="lecture.mp3",
        content=b"fake-audio-bytes",
        language="ar",
        prompt=None,
        diarize=False,
    )
    assert result.usage.audio_seconds == 60.0
    assert result.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_openai_embed_computes_real_cost_from_usage_tokens() -> None:
    provider = _openai_provider()
    fake_response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2]), SimpleNamespace(embedding=[0.3, 0.4])],
        usage=SimpleNamespace(total_tokens=1_000_000),
    )
    provider.client.embeddings.create = AsyncMock(return_value=fake_response)
    result = await provider.embed(model="text-embedding-3-small", texts=["a", "b"])
    assert len(result.vectors) == 2
    # 1,000,000 tokens * 0.02 USD/million (config/pricing.yaml) = 0.02.
    assert result.estimated_cost_usd == 0.02


@pytest.mark.asyncio
async def test_openai_embed_of_empty_texts_costs_nothing() -> None:
    provider = _openai_provider()
    result = await provider.embed(model="text-embedding-3-small", texts=[])
    assert result.vectors == []
    assert result.estimated_cost_usd == 0.0


@pytest.mark.asyncio
async def test_gemini_embed_estimates_a_nonzero_cost_from_input_length() -> None:
    # Matches the real installed google-genai==1.75.0 behavior: embedContent's
    # EmbedContentResponse has no usage_metadata attribute at all (see
    # gemini_provider.embed's comment), so this must fall back to the
    # char-count estimate rather than raising or silently costing $0.
    provider = _gemini_provider()
    fake_response = SimpleNamespace(
        embeddings=[SimpleNamespace(values=[0.1, 0.2]), SimpleNamespace(values=[0.3, 0.4])]
    )
    provider._client.aio.models.embed_content = AsyncMock(return_value=fake_response)
    result = await provider.embed(model="gemini-embedding-001", texts=["a" * 400, "b" * 400])
    assert len(result.vectors) == 2
    assert result.usage.input_tokens > 0
    assert result.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_gemini_embed_uses_exact_usage_when_the_sdk_reports_it() -> None:
    # Forward-compatibility: if a future google-genai release starts exposing
    # usage_metadata.prompt_token_count on EmbedContentResponse (the API
    # already sends it, per ai.google.dev/api/embeddings -- only today's SDK
    # drops it), exact usage must win over the char/4 estimate.
    provider = _gemini_provider()
    fake_response = SimpleNamespace(
        embeddings=[SimpleNamespace(values=[0.1, 0.2]), SimpleNamespace(values=[0.3, 0.4])],
        usage_metadata=SimpleNamespace(prompt_token_count=7),
    )
    provider._client.aio.models.embed_content = AsyncMock(return_value=fake_response)
    result = await provider.embed(model="gemini-embedding-001", texts=["a" * 400, "b" * 400])
    assert result.usage.input_tokens == 7
    assert result.estimated_cost_usd == get_cost_calculator().embedding_cost(
        "gemini-embedding-001", input_tokens=7
    )
