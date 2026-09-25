"""Keep retrieval plumbing out of what a learner reads.

SOURCE_CONTEXT labels its excerpts [S1], [S2] ... so the model can fill
`source_references` and citations. Those labels are meaningless to a student,
yet they leaked into questions and titles ("according to the text in S4",
"(S1)", "source number 9"). The prompts forbid it; this is the safety net,
applied to every learner-facing string of a result. Reference and evidence
fields are left exactly as they are.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

# Arabic is written as ASCII escapes (the linter flags lookalike characters):
# u060c is the Arabic comma, u2013 an en dash.
_LIST_SEPARATOR = "[\\u060c,\\u2013-]"
_BRACKETED = re.compile(
    r"\s*[\(\[\{]\s*S\d+(?:\s*" + _LIST_SEPARATOR + r"\s*S\d+)*\s*[\)\]\}]"
)
_BARE = re.compile(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])")
# "(excerpts from) source (number) 9" in Arabic: dropped when parenthetical,
# reduced to "the source" in running text.
_SOURCE_WORD_AR = "\u0627\u0644\u0645\u0635\u062f\u0631"  # "the source"
_EXCERPTS_FROM = r"(?:\u0645\u0642\u062a\u0637\u0641\u0627\u062a\s+\u0645\u0646\s+)?"
_NUMBER = r"\s+(?:\u0631\u0642\u0645\s+)?\d+"
_SOURCE_NUMBER_PAREN = re.compile(
    r"\s*\(\s*" + _EXCERPTS_FROM + _SOURCE_WORD_AR + _NUMBER + r"\s*\)"
)
_SOURCE_NUMBER = re.compile(_SOURCE_WORD_AR + _NUMBER)
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_SPACES = re.compile(r"[ \t]{2,}")

#: Keys whose values are references/evidence, not prose for the learner.
_UNTOUCHED = {
    "citations",
    "source_references",
    "source_id",
    "source_ids",
    "content_sha256",
    "chunk_id",
    "evidence_id",
    "excerpt",
    "subject_id",
}


def strip_source_labels(text: str) -> str:
    word = _SOURCE_WORD_AR if _ARABIC.search(text) else "the source"
    cleaned = _BRACKETED.sub("", text)
    cleaned = _SOURCE_NUMBER_PAREN.sub("", cleaned)
    cleaned = _SOURCE_NUMBER.sub(_SOURCE_WORD_AR, cleaned)
    cleaned = _BARE.sub(word, cleaned)
    cleaned = _SPACES.sub(" ", cleaned)
    return cleaned.replace(" \u060c", "\u060c").replace(" .", ".").strip()


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return strip_source_labels(value)
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: (item if key in _UNTOUCHED else _clean(item)) for key, item in value.items()}
    return value


def clean_student_text[ModelT: BaseModel](result: ModelT) -> ModelT:
    """The same result with source labels removed from learner-facing text."""
    return type(result).model_validate(_clean(result.model_dump(mode="python")))
