"""What the OpenAI transcription request looks like for each model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.audio.chunking import audio_upload_filename
from app.models.enums import ProviderAccount
from app.providers.openai_provider import OpenAIProvider


def _provider() -> OpenAIProvider:
    return OpenAIProvider(
        account=ProviderAccount.PRIMARY,
        api_key="test-key",
        project_id=None,
        organization_id=None,
        base_url="https://api.openai.test/v1",
        store_responses=False,
        timeout_seconds=30,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "diarize", "expected"),
    [
        # Production 2026-09-24: verbose_json was sent to every model and the
        # gpt-4o family refused it ("Use 'json' or 'text' instead").
        ("gpt-4o-mini-transcribe", False, "json"),
        ("gpt-4o-transcribe", False, "json"),
        ("whisper-1", False, "verbose_json"),
        ("gpt-4o-mini-transcribe", True, "diarized_json"),
    ],
)
async def test_response_format_matches_the_model(
    model: str, diarize: bool, expected: str
) -> None:
    provider = _provider()
    create = AsyncMock(
        return_value=SimpleNamespace(
            text="ok", usage=None, id="resp-1", segments=None, duration=None
        )
    )
    provider.client.audio.transcriptions.create = create  # type: ignore[method-assign]

    await provider.transcribe(
        model=model, filename="audio.wav", content=b"x", language="ar", prompt=None, diarize=diarize
    )

    assert create.await_args is not None
    assert create.await_args.kwargs["response_format"] == expected


@pytest.mark.parametrize(
    ("mime_type", "filename"),
    [
        ("audio/wav", "audio.wav"),
        ("audio/x-wav", "audio.wav"),
        ("audio/mpeg", "audio.mp3"),
        ("audio/x-m4a", "audio.m4a"),
        ("audio/ogg; codecs=opus", "audio.ogg"),
        ("application/octet-stream", "audio.wav"),
    ],
)
def test_upload_filename_names_the_format(mime_type: str, filename: str) -> None:
    assert audio_upload_filename(mime_type) == filename
