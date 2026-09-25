"""A provider's malformed output hands the job to the next candidate."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.errors import ValidationFailure
from app.models.ai_job import AIJob
from app.models.enums import Provider, ProviderAccount, QualityTier, TaskType
from app.prompts.registry import PromptSpec
from app.providers.candidate import ProviderCandidate
from app.providers.mock_provider import MockProvider
from app.schemas.fahes import FahesResult
from app.services.generation import StructuredGenerationService
from app.services.provider_budget import BudgetReservation


class _Session:
    def add(self, _obj: object) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


class _Circuit:
    async def record_success(self, _account: object) -> None:
        pass

    async def record_failure(self, _account: object) -> None:
        pass


class _Budget:
    async def reserve(self, _account: object, _max_cost: float) -> BudgetReservation:
        return BudgetReservation(account=ProviderAccount.PRIMARY, reserved_usd=0.01, granted=True)

    async def commit_actual(self, *_: object) -> None:
        pass

    async def release(self, *_: object, **__: object) -> None:
        pass


class _Settings:
    provider_max_retries = 0


def _job() -> AIJob:
    job = AIJob(
        id=uuid.uuid4(),
        user_id="user-1",
        request_id=str(uuid.uuid4()),
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        idempotency_hash="hash",
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


class _Broken:
    """Stops mid-JSON, as a model repeating itself until max_output_tokens does."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, **_: Any) -> Any:
        self.calls += 1
        raise ValidationFailure("Provider output is not valid JSON")


class _Healthy:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.calls += 1
        return await MockProvider().generate_structured(**kwargs)


class _Router:
    def __init__(self, candidates: list[ProviderCandidate]) -> None:
        self._candidates = candidates
        self.settings = _Settings()
        self.circuit = _Circuit()

    async def ordered_candidates(self, *_: object, **__: object) -> list[ProviderCandidate]:
        return self._candidates


def _candidate(account: ProviderAccount, instance: object) -> ProviderCandidate:
    return ProviderCandidate(
        provider=Provider.OPENAI,
        account_id=account,
        model="mock-balanced",
        task_type="fahes_generate_quiz",
        quality_tier=QualityTier.BALANCED,
        timeout_seconds=30,
        max_output_tokens=800,
        budget_policy="standard",
        pricing_version="v1",
        priority=1,
        instance=instance,  # type: ignore[arg-type]
    )


async def _generate(*, allow_fallback: bool) -> tuple[_Broken, _Healthy, Any]:
    broken, healthy = _Broken(), _Healthy()
    router = _Router(
        [
            _candidate(ProviderAccount.PRIMARY, broken),
            _candidate(ProviderAccount.SECONDARY, healthy),
        ]
    )
    service = StructuredGenerationService(session=_Session(), router=router)  # type: ignore[arg-type]
    service.budget = _Budget()  # type: ignore[assignment]
    job = _job()
    job.allow_fallback = allow_fallback
    result = await service.generate(
        job=job,
        routing=None,  # type: ignore[arg-type]
        prompt=_prompt(),
        user_input="a student's question",
        output_model=FahesResult,
    )
    return broken, healthy, result


@pytest.mark.asyncio
async def test_malformed_output_falls_back_to_the_next_candidate() -> None:
    broken, healthy, result = await _generate(allow_fallback=True)

    assert (broken.calls, healthy.calls) == (1, 1)
    assert result.data


@pytest.mark.asyncio
async def test_without_fallback_the_validation_failure_is_reported() -> None:
    with pytest.raises(ValidationFailure):
        await _generate(allow_fallback=False)
