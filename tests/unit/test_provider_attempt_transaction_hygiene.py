"""RC2 regression: no open database transaction during an external provider call.

Production evidence (see the RC2 stabilization report) showed idle-in-
transaction connections whose age matched how long a real provider call was
in flight -- because the `ProviderAttempt` "STARTED" row was flushed, but
not committed, before the provider call, and only committed afterward in a
`finally`/`except` block. For the whole duration of the call, PostgreSQL saw
an open, idle transaction on that connection.

`ProviderBudgetService.reserve/commit_actual/release` already avoid this by
using their own dedicated short-lived session (see its docstrings in
app/services/provider_budget.py) -- these tests prove the same property now
holds for the `ProviderAttempt` bookkeeping in the two places that record it
around a real external call: StructuredGenerationService.generate() and
SadaPipeline's `_transcribe_with_retry`.

The assertion is call-order, not merely "commit was called": a session fake
that records every call into one shared timeline lets each test assert that
`session.commit()` happened strictly *before* the external call started --
which is exactly the property that determines whether PostgreSQL ever saw an
open transaction while waiting on the network.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.ai_job import AIJob, ProviderAttempt
from app.models.enums import ProviderAccount, QualityTier, TaskType
from app.pipelines.sada import _transcribe_with_retry
from app.prompts.registry import PromptSpec
from app.providers.base import ProviderResult, ProviderUsage
from app.providers.candidate import ProviderCandidate
from app.providers.mock_provider import MockProvider
from app.schemas.fahes import FahesResult
from app.services.generation import StructuredGenerationService


class _RecordingSession:
    """A minimal AsyncSession stand-in that records call order, not state.

    Only implements what StructuredGenerationService.generate() and
    _transcribe_with_retry actually call: add() (sync, ORM-style) and the
    two async methods whose relative ordering this test exists to check.
    """

    def __init__(self, timeline: list[str]) -> None:
        self._timeline = timeline

    def add(self, _obj: object) -> None:
        self._timeline.append("session.add")

    async def flush(self) -> None:
        self._timeline.append("session.flush")

    async def commit(self) -> None:
        self._timeline.append("session.commit")


class _RecordingCircuit:
    async def record_success(self, _account: object) -> None:
        return None

    async def record_failure(self, _account: object) -> None:
        return None


class _RecordingBudget:
    """Stands in for ProviderBudgetService -- already uses its own dedicated
    session in production (see app/services/provider_budget.py), so it is
    legitimately faked here rather than exercised for real."""

    def __init__(self, timeline: list[str]) -> None:
        self._timeline = timeline

    async def reserve(self, _account: object, _max_cost: float) -> object:
        from app.services.provider_budget import BudgetReservation

        return BudgetReservation(account=ProviderAccount.PRIMARY, reserved_usd=0.01, granted=True)

    async def commit_actual(self, _reservation: object, _result: object) -> None:
        self._timeline.append("budget.commit_actual")

    async def release(self, _reservation: object, *_a: object, **_k: object) -> None:
        self._timeline.append("budget.release")


class _RecordingProviderInstance:
    """Stands in for the real LLMProvider.generate_structured -- delegates
    to MockProvider for a schema-valid result, but records exactly when the
    call happened relative to the surrounding session commits."""

    def __init__(self, timeline: list[str]) -> None:
        self._timeline = timeline
        self._mock = MockProvider()

    async def generate_structured(self, **kwargs: object) -> ProviderResult:
        self._timeline.append("provider.generate_structured")
        return await self._mock.generate_structured(**kwargs)  # type: ignore[arg-type]

    async def transcribe(self, **_kwargs: object) -> object:
        from app.providers.base import TranscriptionResult

        self._timeline.append("provider.transcribe")
        return TranscriptionResult(
            text="a transcript",
            segments=[],
            account=ProviderAccount.PRIMARY,
            model="mock-transcribe",
            response_id="mock-response",
            usage=ProviderUsage(),
            latency_ms=1,
            estimated_cost_usd=0.0,
        )


class _RouterSettingsStub:
    provider_max_retries = 0


class _RouterStub:
    """Only the surface StructuredGenerationService.generate() touches on
    `self.router` -- deliberately not a real ProviderRouter, which needs
    Redis and full settings wiring unrelated to this test's assertion."""

    def __init__(self, candidate: ProviderCandidate) -> None:
        self._candidate = candidate
        self.settings = _RouterSettingsStub()
        self.circuit = _RecordingCircuit()

    async def ordered_candidates(self, *_a: object, **_k: object) -> list[ProviderCandidate]:
        return [self._candidate]


def _job() -> AIJob:
    job = AIJob(
        id=uuid.uuid4(),
        user_id="user-1",
        request_id=str(uuid.uuid4()),
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        idempotency_hash="hash",
        allow_fallback=False,
        model_tier="balanced",
    )
    job.attempts = []
    return job


def _prompt() -> PromptSpec:
    return PromptSpec(
        name="fahes_generate_quiz",
        version="1",
        task_type="fahes_generate_quiz",
        system_prompt="system",
        user_template="{input}",
        metadata={},
        checksum="checksum",
    )


@pytest.mark.asyncio
async def test_generation_commits_the_started_attempt_before_calling_the_provider() -> None:
    timeline: list[str] = []
    session = _RecordingSession(timeline)
    candidate = ProviderCandidate(
        provider=__import__("app.models.enums", fromlist=["Provider"]).Provider.OPENAI,
        account_id=ProviderAccount.PRIMARY,
        model="mock-balanced",
        task_type="fahes_generate_quiz",
        quality_tier=QualityTier.BALANCED,
        timeout_seconds=30,
        max_output_tokens=800,
        budget_policy="standard",
        pricing_version="v1",
        priority=1,
        instance=_RecordingProviderInstance(timeline),  # type: ignore[arg-type]
    )

    service = StructuredGenerationService(session=session, router=_RouterStub(candidate))  # type: ignore[arg-type]
    service.budget = _RecordingBudget(timeline)  # type: ignore[assignment]

    await service.generate(
        job=_job(),
        # unused by this stubbed router, which ignores its `routing` argument entirely
        routing=None,  # type: ignore[arg-type]
        prompt=_prompt(),
        user_input="a student's question",
        output_model=FahesResult,
    )

    provider_index = timeline.index("provider.generate_structured")
    commits_before_provider = [
        i for i, event in enumerate(timeline[:provider_index]) if event == "session.commit"
    ]
    assert commits_before_provider, (
        f"no session.commit before the provider call -- timeline was {timeline}. "
        "This means a transaction opened by session.flush() was still open while "
        "awaiting the external provider, exactly the anti-pattern this test guards against."
    )
    # And the STARTED row must have actually been flushed (visible to other
    # connections, e.g. for observability) before that commit -- not just
    # added in memory.
    assert timeline.index("session.flush") < commits_before_provider[0]


@pytest.mark.asyncio
async def test_sada_commits_the_started_attempt_before_calling_transcribe() -> None:
    timeline: list[str] = []
    session = _RecordingSession(timeline)
    candidate = ProviderCandidate(
        provider=__import__("app.models.enums", fromlist=["Provider"]).Provider.OPENAI,
        account_id=ProviderAccount.PRIMARY,
        model="mock-transcribe",
        task_type="sada_transcribe_audio",
        quality_tier=QualityTier.BALANCED,
        timeout_seconds=120,
        max_output_tokens=0,
        budget_policy="standard",
        pricing_version="v1",
        priority=1,
        instance=_RecordingProviderInstance(timeline),  # type: ignore[arg-type]
    )

    await _transcribe_with_retry(
        session=session,  # type: ignore[arg-type]
        budget=_RecordingBudget(timeline),  # type: ignore[arg-type]
        circuit=_RecordingCircuit(),  # type: ignore[arg-type]
        settings=__import__("app.core.config", fromlist=["get_settings"]).get_settings(),
        candidates=[candidate],
        content=b"fake-audio-bytes",
        filename="clip.mp3",
        language="ar",
        prompt=None,
        diarize=False,
        allow_fallback=False,
        job_id=uuid.uuid4(),
        request_id=str(uuid.uuid4()),
        attempt_number_base=0,
    )

    provider_index = timeline.index("provider.transcribe")
    commits_before_provider = [
        i for i, event in enumerate(timeline[:provider_index]) if event == "session.commit"
    ]
    assert commits_before_provider, (
        f"no session.commit before the transcribe call -- timeline was {timeline}. "
        "This means a transaction opened by session.flush() was still open while "
        "awaiting the external transcription provider."
    )
    assert timeline.index("session.flush") < commits_before_provider[0]


def test_provider_attempt_model_is_unaffected_by_the_hygiene_fix() -> None:
    """The fix changes *when* a commit happens, not what gets persisted --
    this pins that ProviderAttempt still carries the same fields it always
    did, so a future refactor of the commit ordering can't silently also
    drop bookkeeping fields."""
    from app.models.enums import ProviderAttemptStatus

    attempt = ProviderAttempt(
        job_id=uuid.uuid4(),
        attempt_number=1,
        provider_account=ProviderAccount.PRIMARY,
        model_name="mock-balanced",
        request_id=str(uuid.uuid4()),
        status=ProviderAttemptStatus.STARTED,
    )
    assert attempt.status == ProviderAttemptStatus.STARTED
