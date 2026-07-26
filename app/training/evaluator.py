from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    schema_valid_rate: float
    source_reference_valid_rate: float
    exact_match_rate: float | None
    average_quality_score: float | None
    sample_count: int


def summarize_evaluations(rows: Iterable[dict[str, Any]]) -> EvaluationMetrics:
    items = list(rows)
    if not items:
        return EvaluationMetrics(0.0, 0.0, None, None, 0)
    schema_valid = sum(bool(item.get("schema_valid")) for item in items)
    refs_valid = sum(bool(item.get("source_references_valid")) for item in items)
    exact_values = [item.get("exact_match") for item in items if item.get("exact_match") is not None]
    quality_values = [
        float(item["quality_score"])
        for item in items
        if item.get("quality_score") is not None
    ]
    return EvaluationMetrics(
        schema_valid_rate=schema_valid / len(items),
        source_reference_valid_rate=refs_valid / len(items),
        exact_match_rate=(sum(bool(value) for value in exact_values) / len(exact_values))
        if exact_values
        else None,
        average_quality_score=sum(quality_values) / len(quality_values)
        if quality_values
        else None,
        sample_count=len(items),
    )
