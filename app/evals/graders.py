"""Deterministic graders (spec section 25: "deterministic graders first").

These check structural/grounding properties that do not require a live
judge model: duplicate detection, citation presence, groundedness floors
already computed by the pipeline/application layer, and policy flags. They
are the first-line gate; LLM-as-judge and human review are later stages this
seed harness does not yet implement (see docs/EVALUATION.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GradeResult:
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def grade_fahes(result: dict[str, Any], validation: dict[str, Any]) -> GradeResult:
    failures: list[str] = []
    metrics: dict[str, float] = {}
    questions = result.get("questions", [])
    if not questions:
        failures.append("no_questions_generated")
    normalized = [str(q.get("question", "")).strip().casefold() for q in questions]
    duplicate_count = len(normalized) - len(set(normalized))
    metrics["duplicate_question_rate"] = duplicate_count / max(1, len(questions))
    if metrics["duplicate_question_rate"] > 0.01:
        failures.append("duplicate_questions_exceeds_threshold")
    citation_present = (
        all(bool(q.get("source_references")) for q in questions) if questions else False
    )
    metrics["citation_presence"] = 1.0 if citation_present else 0.0
    if not citation_present:
        failures.append("missing_source_references")
    metrics["groundedness"] = float(validation.get("groundedness", 0.0))
    return GradeResult(passed=not failures, metrics=metrics, failures=failures)


def grade_kholasa(result: dict[str, Any], validation: dict[str, Any]) -> GradeResult:
    failures: list[str] = []
    groundedness = float(validation.get("groundedness", 0.0))
    metrics = {"groundedness": groundedness}
    if groundedness < 0.90:
        failures.append("groundedness_below_floor")
    if not result.get("key_points"):
        failures.append("no_key_points")
    return GradeResult(passed=not failures, metrics=metrics, failures=failures)


def grade_khota(result: dict[str, Any], validation: dict[str, Any]) -> GradeResult:
    failures: list[str] = []
    plan_days = result.get("plan_days", [])
    metrics = {"plan_day_count": float(len(plan_days))}
    if not plan_days:
        failures.append("no_plan_days")
    if validation.get("constraint_validation") not in {"PASS", "valid"}:
        failures.append("constraint_validation_not_pass")
    return GradeResult(passed=not failures, metrics=metrics, failures=failures)


def grade_rasheed(result: dict[str, Any], validation: dict[str, Any]) -> GradeResult:
    failures: list[str] = []
    recommendations = result.get("recommendations", [])
    metrics = {"recommendation_count": float(len(recommendations))}
    if not recommendations:
        failures.append("no_recommendations")
    if validation.get("data_policy") != "LAB_SESSION_DATA_ONLY":
        failures.append("missing_authoritative_data_policy")
    return GradeResult(passed=not failures, metrics=metrics, failures=failures)


def grade_sada(result: dict[str, Any], validation: dict[str, Any]) -> GradeResult:
    failures: list[str] = []
    if not result.get("cleaned_transcript"):
        failures.append("missing_cleaned_transcript")
    if not validation.get("raw_transcript_preserved"):
        failures.append("raw_transcript_not_preserved")
    return GradeResult(passed=not failures, metrics={}, failures=failures)


GRADERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], GradeResult]] = {
    "fahes_generate_quiz": grade_fahes,
    "kholasa_generate_summary": grade_kholasa,
    "khota_generate_plan": grade_khota,
    "rasheed_recommendations": grade_rasheed,
    "sada_transcribe_audio": grade_sada,
}
