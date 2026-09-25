"""The learner's own words, handed to a model as data it should honour.

A learner can ask for a focus ("the second unit"), a difficulty or a style.
That text is placed in its own LEARNER_INSTRUCTIONS section, never merged
into the system rules, so it can steer what is produced but not how the
source-only and safety rules apply (the prompts say so explicitly).
"""

from __future__ import annotations

from app.rag.guard import sanitize_untrusted_source
from app.rag.outline import unit_label

MAX_INSTRUCTIONS_CHARS = 1000


def learner_instructions(
    instructions: str | None, *, units: set[int], language: str = "ar"
) -> tuple[str, bool]:
    """The LEARNER_INSTRUCTIONS block, and whether the text was dropped as a
    prompt-injection attempt (units are kept either way)."""
    parts: list[str] = []
    if units:
        names = ", ".join(unit_label(unit, language) for unit in sorted(units))
        label = (
            "Requested scope"
            if language == "en"
            else "النطاق المطلوب"
        )
        parts.append(f"{label}: {names}")
    text = (instructions or "").strip()[:MAX_INSTRUCTIONS_CHARS]
    suspicious = bool(text) and sanitize_untrusted_source(text).suspicious
    if text and not suspicious:
        parts.append(text)
    if parts:
        return "\n".join(parts), suspicious
    none = "None." if language == "en" else "لا توجد."
    return none, suspicious
