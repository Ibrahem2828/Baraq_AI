"""The producer half of the character-result field contract.

Django's materializers translate these payloads into domain objects. That
translation is only safe if both sides agree on the field list, and a field
added here that nobody maps is lost in silence -- the AI bills for producing
it, the schema validates it, and the learner never sees it.

`apps/ai_integration/tests.py::CharacterResultFieldContractTests` in the
backend repository asserts the same lists from its side. Adding a field to a
result schema fails both until it is either mapped or explicitly declared as
not persisted.
"""

from __future__ import annotations

import pytest

from app.schemas.fahes import FahesResult
from app.schemas.kholasa import KholasaResult
from app.schemas.khota import KhotaResult
from app.schemas.rasheed import RasheedResult
from app.schemas.sada import SadaResult

#: Must stay identical to CHARACTER_RESULT_FIELDS in the backend repository.
CHARACTER_RESULT_FIELDS: dict[str, set[str]] = {
    "fahes": {"citations", "covered_topics", "description", "questions", "title", "warnings"},
    "kholasa": {
        "citations",
        "covered_topics",
        "detailed_summary",
        "executive_summary",
        "flashcards",
        "important_terms",
        "key_points",
        "limitations",
        "review_questions",
        "title",
    },
    "khota": {"adaptation_rules", "assumptions", "citations", "plan_days", "strategy_summary", "title"},
    "rasheed": {
        "confidence_note",
        "next_best_action",
        "performance_summary",
        "recommendations",
        "strengths",
        "weaknesses",
    },
    "sada": {
        "cleaned_transcript",
        "detected_topics",
        "duration_seconds",
        "full_transcript",
        "important_terms",
        "language",
        "segments",
        "warnings",
    },
}

_MODELS = {
    "fahes": FahesResult,
    "kholasa": KholasaResult,
    "khota": KhotaResult,
    "rasheed": RasheedResult,
    "sada": SadaResult,
}


@pytest.mark.parametrize("character", sorted(CHARACTER_RESULT_FIELDS))
def test_the_declared_field_list_matches_the_schema(character: str) -> None:
    """Drift on the producer side fails here before it can reach Django."""
    actual = set(_MODELS[character].model_fields)
    declared = CHARACTER_RESULT_FIELDS[character]

    assert actual == declared, (
        f"{character} result schema changed: "
        f"added={sorted(actual - declared)} removed={sorted(declared - actual)}. "
        "Update this list and the matching one in the backend, and decide "
        "whether the backend maps the field or declares it unmapped."
    )


def test_rasheed_next_best_action_is_a_string_not_a_structure() -> None:
    """Django stores this as JSON and historically expected a mapping. The
    shape is load-bearing across the repository boundary, so pin it."""
    annotation = RasheedResult.model_fields["next_best_action"].annotation
    assert annotation is str
