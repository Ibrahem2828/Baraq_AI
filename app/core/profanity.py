"""Baseline profanity/offensive-language redaction.

Baraq_MD_Blueprint 02_AI_PLATFORM.md §3.4 ("سياسة الكلمات البذيئة التي
طلبها المشروع", [مطلوب]): Baraq must never return raw profanity to the user
without educational necessity. Default behavior it specifies:

- In a summary: replace with a neutral description, or drop if it doesn't
  change the meaning.
- In educational output (quiz questions/choices): don't generate profanity
  unless it's a necessary, trusted part of educational context and policy
  allows it.
- In a displayed transcript: a redacted form like `[لفظ محجوب]` is
  acceptable, keeping the timestamp.

This is a dictionary-based baseline (the blueprint's own wording: "قاموس +
classifier عند الحاجة" -- dictionary first, a classifier only if needed).
It replaces a matched whole word with the blueprint's own suggested marker
rather than rewriting or dropping surrounding text, which would need an
extra paid LLM call this deliberately avoids. The word list is a starting
point for product/content policy to extend, not a definitive list.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel

REDACTION_MARKER = "[لفظ محجوب]"

_TOKEN = re.compile(r"[\w؀-ۿ]+", re.UNICODE)
_ARABIC_DIACRITICS_AND_TATWEEL = re.compile(r"[ـً-ْٰ]")

# Deliberately conservative: clearly vulgar/obscene terms only, not slurs
# targeting protected groups (a different, higher-stakes policy question)
# and not mild religious oaths (a cultural-register question, not
# profanity). Product/content policy should extend this list.
_PROFANITY_TERMS: frozenset[str] = frozenset(
    {
        # Arabic
        "كس",
        "كسم",
        "كسمك",
        "زب",
        "زبي",
        "طيز",
        "شرموط",
        "شرموطة",
        "قحبة",
        "قحبه",
        "منيك",
        "متناك",
        "عرص",
        "خول",
        "نيك",
        "نيج",
        "احا",
        # English
        "fuck",
        "fucking",
        "shit",
        "bitch",
        "asshole",
        "cunt",
        "dick",
        "pussy",
        "bastard",
        "whore",
        "slut",
    }
)


def _normalize(token: str) -> str:
    token = _ARABIC_DIACRITICS_AND_TATWEEL.sub("", token)
    return unicodedata.normalize("NFKC", token).casefold()


def redact_profanity(text: str) -> tuple[str, bool]:
    """Return (redacted_text, had_profanity). Matches whole words only, so
    it never mangles legitimate text that merely contains a banned term as
    a substring."""
    hit = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal hit
        if _normalize(match.group(0)) in _PROFANITY_TERMS:
            hit = True
            return REDACTION_MARKER
        return match.group(0)

    redacted = _TOKEN.sub(_replace, text)
    return redacted, hit


def redact_model_text[ModelT: BaseModel](
    model: ModelT, *, text_fields: tuple[str, ...] = (), list_fields: tuple[str, ...] = ()
) -> tuple[ModelT, bool]:
    """Redact a pydantic model's plain-string fields and list[str] fields in
    place (via model_copy), reused across every pipeline's output schema
    instead of hand-writing the same field-by-field redaction per pipeline.
    """
    updates: dict[str, object] = {}
    any_hit = False
    for field in text_fields:
        redacted, hit = redact_profanity(getattr(model, field))
        if hit:
            updates[field] = redacted
            any_hit = True
    for field in list_fields:
        redacted_items: list[str] = []
        field_hit = False
        for value in getattr(model, field):
            redacted, hit = redact_profanity(value)
            redacted_items.append(redacted)
            field_hit = field_hit or hit
        if field_hit:
            updates[field] = redacted_items
            any_hit = True
    if updates:
        model = model.model_copy(update=updates)
    return model, any_hit


def redact_model_list[ModelT: BaseModel](
    items: list[ModelT],
    *,
    text_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
) -> tuple[list[ModelT], bool]:
    """redact_model_text applied across a list of models (e.g. quiz
    questions, flashcards), returning the rebuilt list and whether any item
    needed redaction."""
    updated: list[ModelT] = []
    any_hit = False
    for item in items:
        updated_item, hit = redact_model_text(
            item, text_fields=text_fields, list_fields=list_fields
        )
        updated.append(updated_item)
        any_hit = any_hit or hit
    return updated, any_hit
