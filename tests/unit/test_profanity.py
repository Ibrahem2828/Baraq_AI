from __future__ import annotations

from pydantic import BaseModel

from app.core.profanity import (
    REDACTION_MARKER,
    redact_model_list,
    redact_model_text,
    redact_profanity,
)


class _Card(BaseModel):
    front: str
    back: str


class _Note(BaseModel):
    title: str
    tags: list[str]


def test_redacts_a_known_arabic_term() -> None:
    redacted, hit = redact_profanity("هذا الكلام فيه كلمة شرموطة داخله")
    assert hit is True
    assert REDACTION_MARKER in redacted
    assert "شرموطة" not in redacted


def test_redacts_a_known_english_term() -> None:
    redacted, hit = redact_profanity("this is fucking great")
    assert hit is True
    assert REDACTION_MARKER in redacted


def test_leaves_clean_text_untouched() -> None:
    text = "الدرس اليوم عن قوانين نيوتن للحركة"
    redacted, hit = redact_profanity(text)
    assert hit is False
    assert redacted == text


def test_does_not_match_a_clean_word_containing_a_banned_term_as_a_substring() -> None:
    # "زب" is banned; "زبدة" (butter) merely starts with it as a substring
    # and must not be redacted -- only a whole-token match counts.
    text = "أضف ملعقة زبدة إلى الخليط"
    redacted, hit = redact_profanity(text)
    assert hit is False
    assert redacted == text


def test_is_diacritic_and_case_insensitive() -> None:
    _, hit = redact_profanity("FUCK this")
    assert hit is True
    redacted2, hit2 = redact_profanity("شُرْمُوطَة")
    assert hit2 is True
    assert redacted2 == REDACTION_MARKER


def test_redact_model_text_updates_only_fields_that_had_a_hit() -> None:
    note = _Note(title="fuck this title", tags=["clean tag", "another shit tag"])
    updated, hit = redact_model_text(note, text_fields=("title",), list_fields=("tags",))
    assert hit is True
    assert updated.title == f"{REDACTION_MARKER} this title"
    assert updated.tags == ["clean tag", f"another {REDACTION_MARKER} tag"]


def test_redact_model_text_is_a_noop_when_nothing_matches() -> None:
    note = _Note(title="clean title", tags=["clean tag"])
    updated, hit = redact_model_text(note, text_fields=("title",), list_fields=("tags",))
    assert hit is False
    assert updated == note


def test_redact_model_list_redacts_each_item_independently() -> None:
    cards = [_Card(front="clean", back="also clean"), _Card(front="fuck", back="clean back")]
    updated, hit = redact_model_list(cards, text_fields=("front", "back"))
    assert hit is True
    assert updated[0] == cards[0]
    assert updated[1].front == REDACTION_MARKER
    assert updated[1].back == "clean back"
