from __future__ import annotations

import pytest

from app.core.errors import ValidationFailure
from app.rag.grounding import validate_topic_references


def test_passes_when_cited_topic_is_known() -> None:
    validate_topic_references(known_topics={"algebra"}, cited_topics=["Algebra"])


def test_rejects_a_topic_absent_from_the_known_set() -> None:
    with pytest.raises(ValidationFailure) as error:
        validate_topic_references(known_topics={"algebra"}, cited_topics=["quantum physics"])
    assert error.value.code == "unsupported_topic_reference"


def test_is_a_noop_when_the_caller_has_no_topic_data_to_ground_against() -> None:
    # Nothing to check against -- must not block callers with no topic data
    # (matches the existing Khota "if request.source_ids" style gate: a
    # grounding check only applies when there is something to ground against).
    validate_topic_references(known_topics=set(), cited_topics=["anything"])
