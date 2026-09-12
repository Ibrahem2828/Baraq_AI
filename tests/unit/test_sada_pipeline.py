"""Regression tests for the Sada token-waste fixes (spec section 6 / audit
P1-P2): the LLM must not be asked to regenerate full_transcript/segments/
language/duration_seconds (STT- and request-owned data), the prompt must not
duplicate the transcript's words in both raw text and per-segment JSON, and
cleanup_level="literal" must make no LLM call at all."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.models.ai_job import AIJob
from app.models.enums import Character, JobStatus, Provider, ProviderAccount, QualityTier, TaskType
from app.pipelines.base import PipelineContext
from app.pipelines.sada import SadaPipeline
from app.providers.base import ProviderResult, ProviderUsage, TranscriptionResult
from app.providers.candidate import ProviderCandidate
from app.providers.capabilities import Capability
from app.schemas.sada import SadaCleanupResult, SadaResult

RAW_TEXT = "هذا نص تجريبي طويل من محاضرة تعليمية حول موضوع معين."
SEGMENTS = [
    {"start": 0.0, "end": 3.0, "text": "هذا نص تجريبي طويل", "speaker": "S1", "confidence": 0.9},
    {"start": 3.0, "end": 6.0, "text": "من محاضرة تعليمية حول موضوع معين.", "speaker": "S1"},
]
AUDIO_SECONDS = 6.0


class FakeCircuit:
    async def record_success(self, account: ProviderAccount) -> None:
        pass

    async def record_failure(self, account: ProviderAccount) -> None:
        pass


class FakeBudget:
    """Budget correctness has its own dedicated suite
    (tests/unit/test_provider_budget.py) -- this fake just always grants, so
    these tests isolate the Sada-specific schema/prompt/dedup logic."""

    async def reserve(self, account: ProviderAccount, max_cost_usd: float) -> Any:
        from app.services.provider_budget import BudgetReservation

        return BudgetReservation(account=account, reserved_usd=max_cost_usd, granted=True)

    async def release(self, reservation: Any) -> None:
        pass

    async def commit_actual(self, reservation: Any, result: Any) -> None:
        pass


class FakeTranscriptionProvider:
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
        return TranscriptionResult(
            text=RAW_TEXT,
            segments=[dict(segment) for segment in SEGMENTS],
            account=ProviderAccount.PRIMARY,
            model=model,
            response_id="resp-stt",
            usage=ProviderUsage(input_tokens=0, output_tokens=0, audio_seconds=AUDIO_SECONDS),
            estimated_cost_usd=0.02,
        )


class FakeRouter:
    def __init__(self, instance: FakeTranscriptionProvider) -> None:
        self.circuit = FakeCircuit()
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


class FakeGeneration:
    def __init__(self, cleanup_result: SadaCleanupResult | None) -> None:
        self.router = FakeRouter(FakeTranscriptionProvider())
        self.budget = FakeBudget()
        self._cleanup_result = cleanup_result
        self.generate_calls: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        job: AIJob,
        routing: Any,
        prompt: Any,
        user_input: str,
        output_model: type,
    ) -> ProviderResult:
        assert self._cleanup_result is not None, "literal mode must never call generate()"
        assert output_model is SadaCleanupResult
        self.generate_calls.append({"user_input": user_input})
        return ProviderResult(
            data=self._cleanup_result.model_dump(mode="json"),
            account=ProviderAccount.PRIMARY,
            model="gpt-5-mini",
            response_id="resp-cleanup",
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            estimated_cost_usd=0.01,
        )


class FakeBackend:
    async def get_source_manifest(
        self, *, source_id: str, user_id: str, project_id: str | None = None
    ) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            source_id=source_id,
            owner_user_id=user_id,
            project_id=project_id,
            title="lecture.mp3",
            mime_type="audio/mpeg",
            size_bytes=4,
            content_sha256="a" * 64,
        )

    async def download_source(self, *, manifest: Any, user_id: str) -> bytes:
        return b"fake"


class FakeSession:
    def add(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


def _job(*, cleanup_level: str) -> AIJob:
    return AIJob(
        id=uuid4(),
        user_id="user-1",
        project_id="project-1",
        request_id=str(uuid4()),
        task_type=TaskType.SADA_TRANSCRIBE_AUDIO,
        character=Character.SADA,
        status=JobStatus.RETRIEVING,
        request_payload={
            "source_id": "source-1",
            "language": "ar",
            "diarize": False,
            "known_terms": [],
            "cleanup_level": cleanup_level,
        },
        source_ids=["source-1"],
        source_versions={"source-1": "a" * 64},
        allow_fallback=True,
    )


async def _run(
    *,
    cleanup_level: str,
    cleanup_result: SadaCleanupResult | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, FakeGeneration]:
    # app/pipelines/sada.py does `from app.utils.hash import sha256_bytes`,
    # binding the name into its own module -- patch it there, not at the
    # source module (same pattern as patching AsyncSessionLocal elsewhere).
    import app.pipelines.sada as sada_module

    monkeypatch.setattr(sada_module, "sha256_bytes", lambda data: "a" * 64)
    generation = FakeGeneration(cleanup_result)
    context = PipelineContext(
        session=FakeSession(),  # type: ignore[arg-type]
        job=_job(cleanup_level=cleanup_level),
        backend=FakeBackend(),  # type: ignore[arg-type]
        generation=generation,  # type: ignore[arg-type]
        ingestion=None,  # type: ignore[arg-type]
    )
    result = await SadaPipeline().execute(context)
    return result, generation


@pytest.mark.asyncio
async def test_literal_mode_never_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    result, generation = await _run(
        cleanup_level="literal", cleanup_result=None, monkeypatch=monkeypatch
    )
    assert generation.generate_calls == []
    payload = SadaResult.model_validate(result.result_json)
    # Whitespace-only normalization -- no words changed.
    assert payload.cleaned_transcript == RAW_TEXT
    assert payload.detected_topics == []
    assert payload.important_terms == []


@pytest.mark.asyncio
async def test_literal_mode_still_attributes_the_real_transcription_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The only paid call in literal mode is transcription -- AIOutput must
    # not show $0 for it (that was exactly the original P0 cost-accounting
    # gap; literal mode must not reopen a version of it).
    result, _ = await _run(cleanup_level="literal", cleanup_result=None, monkeypatch=monkeypatch)
    assert result.provider_result.estimated_cost_usd == 0.02
    assert result.provider_result.model == "gpt-4o-mini-transcribe"


@pytest.mark.parametrize("cleanup_level", ["light", "educational"])
@pytest.mark.asyncio
async def test_non_literal_modes_do_not_let_the_llm_override_stt_owned_fields(
    cleanup_level: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A realistic "lightly cleaned" transcript: same words as RAW_TEXT, only
    # punctuation-adjacent -- transcript_preservation_score (a real safety
    # gate rejecting cleanup that rewrites the audio) requires >=55% term
    # overlap with the raw transcript, so this can't be arbitrary text.
    cleanup_result = SadaCleanupResult(
        cleaned_transcript=RAW_TEXT,
        detected_topics=["موضوع"],
        important_terms=["مصطلح"],
        warnings=["تحذير تجريبي"],
    )
    result, generation = await _run(
        cleanup_level=cleanup_level, cleanup_result=cleanup_result, monkeypatch=monkeypatch
    )
    payload = SadaResult.model_validate(result.result_json)
    # STT/request-owned fields: exactly what STT/the request said, never
    # something the LLM could have invented (the LLM schema doesn't even
    # have these fields anymore -- see SadaCleanupResult).
    assert payload.full_transcript == RAW_TEXT
    assert payload.language == "ar"
    assert payload.duration_seconds == AUDIO_SECONDS
    assert [segment.text for segment in payload.segments] == [s["text"] for s in SEGMENTS]
    assert [segment.start_seconds for segment in payload.segments] == [s["start"] for s in SEGMENTS]
    # AI-owned fields: exactly what the (fake) LLM returned.
    assert payload.cleaned_transcript == RAW_TEXT
    assert payload.detected_topics == ["موضوع"]
    assert payload.warnings == ["تحذير تجريبي"]
    assert len(generation.generate_calls) == 1


@pytest.mark.asyncio
async def test_displayed_transcript_redacts_profanity_but_full_transcript_keeps_the_raw_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same real words as RAW_TEXT plus one profane token appended -- keeps
    # transcript_preservation_score comfortably above its 0.55 floor.
    cleanup_result = SadaCleanupResult(cleaned_transcript=f"{RAW_TEXT} fuck", warnings=[])
    result, _ = await _run(
        cleanup_level="light", cleanup_result=cleanup_result, monkeypatch=monkeypatch
    )
    payload = SadaResult.model_validate(result.result_json)
    assert "fuck" not in payload.cleaned_transcript
    assert "[لفظ محجوب]" in payload.cleaned_transcript
    # The internally-kept raw copy is untouched by the displayed-transcript
    # redaction (blueprint 02_AI_PLATFORM.md §3.4's recommended Raw/Safe split).
    assert payload.full_transcript == RAW_TEXT
    assert "profanity_redacted" in result.warnings


@pytest.mark.asyncio
async def test_prompt_input_does_not_duplicate_segment_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_result = SadaCleanupResult(cleaned_transcript=RAW_TEXT, warnings=[])
    _, generation = await _run(
        cleanup_level="light", cleanup_result=cleanup_result, monkeypatch=monkeypatch
    )
    user_input = generation.generate_calls[0]["user_input"]
    # The raw transcript appears once (inside RAW_TRANSCRIPT); the segment
    # timeline must carry timing/speaker only, not repeat each segment's
    # text a second time (the fixed P2 duplication).
    assert user_input.count(RAW_TEXT) == 1
    for segment in SEGMENTS:
        assert segment["text"] not in user_input.replace(RAW_TEXT, "", 1)
    assert '"speaker": "S1"' in user_input or "'speaker': 'S1'" in user_input
