"""End-to-end test of Sada's chunked path (blueprint 02_AI_PLATFORM.md §8):
a synthetic, real WAV recording is split into multiple chunks, each chunk is
"transcribed" by a fake provider that reports its own call count so the test
can confirm more than one chunk was actually dispatched, and the merged
result is checked for absolute-time correctness and full coverage.

Uses a real SQLite-backed AsyncSessionLocal (matching the pattern in
test_provider_budget.py/test_result_cache.py) because each chunk's
ProviderAttempt bookkeeping opens its own dedicated session -- unlike the
unchunked path's tests, a no-op FakeSession isn't enough here.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.pipelines.sada as sada_module
from app.core.config import get_settings
from app.db.base import Base
from app.models.ai_job import AIJob, ProviderAttempt
from app.models.enums import Character, JobStatus, Provider, ProviderAccount, QualityTier, TaskType
from app.pipelines.base import PipelineContext
from app.pipelines.sada import SadaPipeline
from app.providers.base import ProviderUsage, TranscriptionResult
from app.providers.candidate import ProviderCandidate
from app.providers.capabilities import Capability
from app.schemas.sada import SadaResult
from app.utils.hash import sha256_bytes


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element: object, compiler: object, **kw: object) -> str:
    return "JSON"


SAMPLE_RATE = 16_000


def _tone(duration_s: float, freq: float = 440.0, amplitude: int = 12_000) -> list[int]:
    n = int(SAMPLE_RATE * duration_s)
    return [int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)]


def _silence(duration_s: float) -> list[int]:
    return [0] * int(SAMPLE_RATE * duration_s)


def _wav_bytes(samples: list[int]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


@pytest.fixture
def long_wav() -> bytes:
    # tone(4s) silence(1s) tone(4s) = 9s total, with one clear silence gap
    # a real VAD pass should find near the 4s mark.
    return _wav_bytes(_tone(4) + _silence(1) + _tone(4))


class _FakeCircuit:
    async def record_success(self, account: ProviderAccount) -> None:
        pass

    async def record_failure(self, account: ProviderAccount) -> None:
        pass


class _FakeBudget:
    async def reserve(self, account: ProviderAccount, max_cost_usd: float) -> Any:
        from app.services.provider_budget import BudgetReservation

        return BudgetReservation(account=account, reserved_usd=max_cost_usd, granted=True)

    async def release(self, reservation: Any) -> None:
        pass

    async def commit_actual(self, reservation: Any, result: Any) -> None:
        pass


class _CountingTranscriptionProvider:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def transcribe(
        self,
        *,
        model: str,
        filename: str,
        content: bytes,
        language: str | None,
        prompt: str | None,
        diarize: bool,
    ) -> TranscriptionResult:
        index = len(self.calls)
        self.calls.append(content)
        # Local (chunk-relative) segment timing -- the pipeline is
        # responsible for offsetting this to the chunk's absolute position.
        return TranscriptionResult(
            text=f"chunk {index} content",
            segments=[{"start": 0.2, "end": 1.0, "text": f"chunk {index} content"}],
            account=ProviderAccount.PRIMARY,
            model=model,
            response_id=f"resp-{index}",
            usage=ProviderUsage(input_tokens=0, output_tokens=0, audio_seconds=1.0),
            estimated_cost_usd=0.01,
        )


class _FakeRouter:
    def __init__(self, instance: _CountingTranscriptionProvider) -> None:
        self.circuit = _FakeCircuit()
        self._instance = instance

    async def candidates_for_capability(
        self, capability: Capability, routing_key: str, *, allow_fallback: bool = True
    ) -> list[ProviderCandidate]:
        return [
            ProviderCandidate(
                provider=Provider.OPENAI,
                account_id=ProviderAccount.PRIMARY,
                model="gpt-4o-mini-transcribe",
                task_type=TaskType.SADA_TRANSCRIBE_AUDIO.value,
                quality_tier=QualityTier.BALANCED,
                timeout_seconds=60,
                max_output_tokens=4000,
                budget_policy="default",
                pricing_version="test",
                priority=1,
                instance=self._instance,  # type: ignore[arg-type]
            )
        ]


class _FakeGeneration:
    def __init__(self, provider: _CountingTranscriptionProvider) -> None:
        self.router = _FakeRouter(provider)
        self.budget = _FakeBudget()

    async def generate(self, **kwargs: Any) -> Any:
        raise AssertionError("cleanup_level='literal' must never call generate()")


class _FakeBackend:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.content_sha256 = sha256_bytes(content)

    async def get_source_manifest(
        self, *, source_id: str, user_id: str, project_id: str | None = None
    ) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            source_id=source_id,
            owner_user_id=user_id,
            project_id=project_id,
            title="lecture.wav",
            mime_type="audio/wav",
            size_bytes=len(self._content),
            content_sha256=self.content_sha256,
        )

    async def download_source(self, *, manifest: Any, user_id: str) -> bytes:
        return self._content


class _RecordingSession:
    """Records job.progress_message at each commit -- the main session the
    pipeline itself uses; each chunk's ProviderAttempt bookkeeping uses its
    own real SQLite AsyncSessionLocal (patched in below), not this one."""

    def __init__(self, job: AIJob) -> None:
        self.job = job
        self.progress_history: list[str] = []

    def add(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.progress_history.append(self.job.progress_message)


@pytest.fixture
async def sqlite_attempt_session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[Any]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'attempts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[ProviderAttempt.__table__])  # type: ignore[list-item]
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_long_recording_is_split_transcribed_and_merged(
    long_wav: bytes,
    sqlite_attempt_session_factory: async_sessionmaker[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("SADA_CHUNK_THRESHOLD_SECONDS", "3")
    monkeypatch.setenv("SADA_CHUNK_TARGET_SECONDS", "4")
    monkeypatch.setenv("SADA_CHUNK_OVERLAP_SECONDS", "1")
    monkeypatch.setenv("SADA_CHUNK_SILENCE_SEARCH_WINDOW_SECONDS", "2")
    monkeypatch.setenv("SADA_CHUNK_MIN_SILENCE_LEN_MS", "500")
    monkeypatch.setattr(sada_module, "AsyncSessionLocal", sqlite_attempt_session_factory)

    provider = _CountingTranscriptionProvider()
    generation = _FakeGeneration(provider)
    backend = _FakeBackend(long_wav)
    job = AIJob(
        id=uuid4(),
        user_id="user-1",
        project_id="project-1",
        request_id=str(uuid4()),
        task_type=TaskType.SADA_TRANSCRIBE_AUDIO,
        character=Character.SADA,
        status=JobStatus.RETRIEVING,
        progress_message="تم استلام الطلب",
        request_payload={
            "source_id": "source-1",
            "language": "ar",
            "diarize": False,
            "known_terms": [],
            "cleanup_level": "literal",
        },
        source_ids=["source-1"],
        source_versions={"source-1": backend.content_sha256},
        allow_fallback=True,
    )
    session = _RecordingSession(job)
    context = PipelineContext(
        session=session,  # type: ignore[arg-type]
        job=job,
        backend=backend,  # type: ignore[arg-type]
        generation=generation,  # type: ignore[arg-type]
        ingestion=None,  # type: ignore[arg-type]
    )

    try:
        result = await SadaPipeline().execute(context)
    finally:
        get_settings.cache_clear()

    # More than one chunk was actually dispatched to the provider.
    assert len(provider.calls) > 1

    payload = SadaResult.model_validate(result.result_json)
    # Segments are in non-decreasing absolute time order and collectively
    # reach close to the true 9-second recording.
    starts = [segment.start_seconds for segment in payload.segments]
    assert starts == sorted(starts)
    assert payload.duration_seconds is not None
    assert 8.5 <= payload.duration_seconds <= 9.5

    assert "تم تجهيز الصوت" in session.progress_history
    assert any(msg.startswith("تمت معالجة") for msg in session.progress_history)
    assert "جارٍ دمج النص" in session.progress_history
