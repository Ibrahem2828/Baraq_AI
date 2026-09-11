from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.cost import CostCalculator


def _calculator() -> CostCalculator:
    return CostCalculator(get_settings().pricing_config_path)


def test_audio_cost_is_nonzero_for_a_priced_transcription_model() -> None:
    calculator = _calculator()
    cost = calculator.audio_cost("gpt-4o-mini-transcribe", seconds=120.0)
    # 120s = 2 minutes * 0.003 USD/minute (config/pricing.yaml) = 0.006.
    assert cost == 0.006


def test_audio_cost_is_zero_seconds_for_zero_duration() -> None:
    calculator = _calculator()
    assert calculator.audio_cost("gpt-4o-mini-transcribe", seconds=0.0) == 0.0


def test_audio_cost_is_zero_for_an_unpriced_model_not_silently_wrong() -> None:
    calculator = _calculator()
    assert calculator.audio_cost("not-a-real-model", seconds=120.0) == 0.0


def test_embedding_cost_is_nonzero_for_a_priced_embedding_model() -> None:
    calculator = _calculator()
    cost = calculator.embedding_cost("text-embedding-3-small", input_tokens=1_000_000)
    # 1,000,000 tokens * 0.02 USD/million (config/pricing.yaml) = 0.02.
    assert cost == 0.02


def test_embedding_cost_scales_with_token_count() -> None:
    calculator = _calculator()
    small = calculator.embedding_cost("gemini-embedding-001", input_tokens=100)
    large = calculator.embedding_cost("gemini-embedding-001", input_tokens=1000)
    assert large == pytest.approx(small * 10)
    assert small > 0.0


@pytest.mark.parametrize(
    ("model", "input_per_million", "output_per_million"),
    [
        ("gpt-4o-transcribe", 2.50, 10.00),
        ("gpt-4o-mini-transcribe", 1.25, 5.00),
        ("gpt-4o-transcribe-diarize", 2.50, 10.00),
    ],
)
def test_transcription_cost_bills_gpt4o_transcribe_family_by_token_not_duration(
    model: str, input_per_million: float, output_per_million: float
) -> None:
    # These models are billed per audio/text token by OpenAI, not per minute
    # -- audio_per_minute on these entries is only a published estimate, so a
    # long silence (few tokens, many seconds) or dense speech (many tokens,
    # few seconds) must follow token usage, not duration.
    calculator = _calculator()
    cost = calculator.transcription_cost(
        model, seconds=1.0, input_tokens=1_000_000, output_tokens=1_000_000
    )
    expected = round(input_per_million + output_per_million, 8)
    assert cost == expected
    duration_only_estimate = calculator.audio_cost(model, seconds=1.0)
    assert cost != duration_only_estimate


def test_transcription_cost_bills_whisper_by_duration_since_it_reports_no_usage() -> None:
    calculator = _calculator()
    # whisper-1 never returns token usage.
    cost = calculator.transcription_cost(
        "whisper-1", seconds=120.0, input_tokens=0, output_tokens=0
    )
    assert cost == calculator.audio_cost("whisper-1", seconds=120.0)
    # 120s = 2 minutes * 0.006 USD/minute (config/pricing.yaml) = 0.012.
    assert cost == 0.012


def test_transcription_cost_falls_back_to_estimate_when_a_token_model_reports_no_usage() -> None:
    # Defensive fallback: if OpenAI ever omits `usage` for a token-priced
    # model, still bill something (the published estimate) rather than $0.
    calculator = _calculator()
    cost = calculator.transcription_cost(
        "gpt-4o-mini-transcribe", seconds=120.0, input_tokens=0, output_tokens=0
    )
    assert cost == calculator.audio_cost("gpt-4o-mini-transcribe", seconds=120.0)
    assert cost > 0.0


def test_max_generation_cost_uses_max_output_tokens_as_the_ceiling() -> None:
    calculator = _calculator()
    cost = calculator.max_generation_cost(
        "gpt-5-mini", estimated_input_tokens=1_000_000, max_output_tokens=1_000_000
    )
    # 1M input * 0.25/M + 1M output * 2.00/M (config/pricing.yaml) = 2.25.
    assert cost == 2.25
    assert cost == calculator.token_cost(
        "gpt-5-mini", input_tokens=1_000_000, output_tokens=1_000_000
    )


def test_max_transcription_cost_is_the_published_per_minute_estimate() -> None:
    calculator = _calculator()
    cost = calculator.max_transcription_cost("gpt-4o-mini-transcribe", max_seconds=120.0)
    assert cost == calculator.audio_cost("gpt-4o-mini-transcribe", seconds=120.0)
    assert cost == 0.006
