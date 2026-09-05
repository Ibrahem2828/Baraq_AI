from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailure
from app.providers.replay_provider import ReplayProvider
from app.schemas.fahes import FahesResult


def _provider() -> ReplayProvider:
    return ReplayProvider(fixtures_dir=get_settings().replay_fixtures_dir)


@pytest.mark.asyncio
async def test_replay_loads_the_success_fixture_and_passes_real_schema_validation() -> None:
    provider = _provider()
    result = await provider.generate_structured(
        model="replay",
        instructions="",
        user_input="",
        output_model=FahesResult,
        schema_name="fahes_generate_quiz",
        max_output_tokens=100,
        reasoning_effort=None,
        metadata={"task_type": "fahes_generate_quiz"},
    )
    FahesResult.model_validate(result.data)
    assert result.metadata["simulated"] is True
    assert result.metadata["scenario"] == "success"


@pytest.mark.asyncio
async def test_replay_schema_mismatch_fixture_fails_real_validation() -> None:
    provider = _provider()
    with pytest.raises(ValidationFailure):
        await provider.generate_structured(
            model="replay",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="fahes_generate_quiz",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "fahes_generate_quiz", "replay_scenario": "schema_mismatch"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected_code,expected_retryable",
    [
        ("provider_timeout", "provider_timeout", True),
        ("provider_rate_limited", "provider_rate_limited", True),
        ("provider_unavailable", "provider_unavailable", True),
        ("auth_failed", "auth_failed", False),
    ],
)
async def test_replay_common_negative_scenarios_from_the_shared_fixture_pool(
    scenario: str, expected_code: str, expected_retryable: bool
) -> None:
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate_structured(
            model="replay",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="fahes_generate_quiz",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "fahes_generate_quiz", "replay_scenario": scenario},
        )
    assert exc.value.code == expected_code
    assert exc.value.retryable is expected_retryable


@pytest.mark.asyncio
async def test_replay_empty_output_scenario_is_retryable() -> None:
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate_structured(
            model="replay",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="fahes_generate_quiz",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "fahes_generate_quiz", "replay_scenario": "empty_output"},
        )
    assert exc.value.code == "empty_provider_output"
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_replay_invalid_json_scenario_is_a_validation_failure_not_a_provider_error() -> None:
    provider = _provider()
    with pytest.raises(ValidationFailure) as exc:
        await provider.generate_structured(
            model="replay",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="fahes_generate_quiz",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "fahes_generate_quiz", "replay_scenario": "invalid_json"},
        )
    assert exc.value.code == "provider_invalid_json"


@pytest.mark.asyncio
async def test_replay_raises_a_clear_error_for_a_missing_fixture() -> None:
    provider = _provider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate_structured(
            model="replay",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="not_a_real_task",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "not_a_real_task"},
        )
    assert exc.value.code == "replay_fixture_missing"
