from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core.errors import ProviderError
from app.providers.mock_provider import MockProvider
from app.schemas.fahes import FahesResult
from app.schemas.kholasa import KholasaResult
from app.schemas.khota import KhotaNarrative
from app.schemas.rasheed import RasheedResult
from app.schemas.sada import SadaCleanupResult

KNOWN_TASKS: list[tuple[str, type[BaseModel]]] = [
    ("fahes_generate_quiz", FahesResult),
    ("kholasa_generate_summary", KholasaResult),
    ("khota_generate_plan", KhotaNarrative),
    ("rasheed_recommendations", RasheedResult),
    ("sada_transcribe_audio", SadaCleanupResult),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type,output_model", KNOWN_TASKS)
async def test_mock_provider_returns_a_schema_valid_result_for_every_known_task(
    task_type: str, output_model: type[BaseModel]
) -> None:
    provider = MockProvider()
    result = await provider.generate_structured(
        model="mock-balanced",
        instructions="",
        user_input="",
        output_model=output_model,
        schema_name=task_type,
        max_output_tokens=100,
        reasoning_effort=None,
        metadata={"task_type": task_type},
    )
    output_model.model_validate(result.data)
    assert result.metadata["simulated"] is True
    assert result.metadata["provider_mode"] == "mock"
    assert result.estimated_cost_usd == 0.0


@pytest.mark.asyncio
async def test_mock_provider_raises_for_an_unregistered_task_type() -> None:
    provider = MockProvider()
    with pytest.raises(ProviderError) as exc:
        await provider.generate_structured(
            model="mock-balanced",
            instructions="",
            user_input="",
            output_model=FahesResult,
            schema_name="not_a_real_task",
            max_output_tokens=100,
            reasoning_effort=None,
            metadata={"task_type": "not_a_real_task"},
        )
    assert exc.value.code == "mock_fixture_missing"


@pytest.mark.asyncio
async def test_mock_provider_embed_and_moderate_never_touch_the_network() -> None:
    provider = MockProvider()
    result = await provider.embed(model="mock", texts=["a", "b"])
    assert len(result.vectors) == 2
    moderation = await provider.moderate(model="mock", inputs=["x"])
    assert moderation[0]["flagged"] is False
