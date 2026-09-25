"""Where a source's units are, so a request can be scoped to them.

Textbooks repeat the unit on every page as a running header ("الوحدة
الثانية"), and the extractor keeps it in each page's text. That is a far more
reliable signal than similarity search for a learner asking "focus on unit
two": similarity to the words "the second unit" finds nothing useful. It also
separates front matter (cover, table of contents, authoring committee) from
the content: the contents pages name several units at once, content pages
name exactly one.

Everything here is a pure function over chunk texts in reading order.
"""

from __future__ import annotations

import re

# Arabic is written as escapes (the linter flags lookalike characters).
_UNIT_WORD = "الوحدة"  # the unit
_UNIT_WORDS = (
    "الوحد(?:ة|تين|تان|ات)"
)  # unit / two units / units

# Ordinals, longest first so a prefix never shadows a full word. "الأو" is
# how extraction renders "الأولى" when its final ligature is lost.
_ORDINALS: tuple[tuple[str, int], ...] = (
    ("الأولى", 1),  # الأولى
    ("الأول", 1),  # الأول
    ("الأو", 1),  # الأو (truncated)
    ("الثانية", 2),
    ("الثاني", 2),
    ("الثالثة", 3),
    ("الثالث", 3),
    ("الرابعة", 4),
    ("الرابع", 4),
    ("الخامسة", 5),
    ("الخامس", 5),
    ("السادسة", 6),
    ("السادس", 6),
    ("السابعة", 7),
    ("السابع", 7),
    ("الثامنة", 8),
    ("الثامن", 8),
    ("التاسعة", 9),
    ("التاسع", 9),
    ("العاشرة", 10),
    ("العاشر", 10),
)
_ORDINAL_VALUE = dict(_ORDINALS)
_ORDINAL_ALTERNATION = "|".join(word for word, _ in _ORDINALS)
_ARABIC_LETTER = "ء-ي"
_DIGITS = "0-9\u0660-\u0669"  # ASCII and Arabic-Indic digits

# "الوحدة الثانية" / "الوحدة 2" as a header: the ordinal must end the word
# ("الوحدة الأوكسينات" is not unit one).
_HEADER = re.compile(
    _UNIT_WORD
    + r"\s+(?:(?P<word>"
    + _ORDINAL_ALTERNATION
    + r")(?![" + _ARABIC_LETTER + r"])|(?P<num>[" + _DIGITS + r"]{1,2})(?![" + _DIGITS + r"]))"
)
_ENGLISH_HEADER = re.compile(r"\b(?:unit|chapter)\s+(?P<num>\d{1,2})\b", re.IGNORECASE)

# A request naming units: "الوحدة الثانية", "الوحدتين الأولى والثالثة",
# "الوحدات 1 و2", "unit 2". Only ordinals directly after the unit word (and
# chained by "و"/comma) count -- "الوحدة الثانية وأعطني 10 أسئلة" is unit 2.
_ONE = (
    r"(?:(?P<word>" + _ORDINAL_ALTERNATION + r")(?![" + _ARABIC_LETTER + r"])"
    r"|(?P<num>[" + _DIGITS + r"]{1,2})(?![" + _DIGITS + r"]))"
)
_REQUEST_UNITS = re.compile(_UNIT_WORDS)
_REQUEST_CHAIN = re.compile(
    _UNIT_WORDS + r"\s+" + _ONE.replace("?P<word>", "?:").replace("?P<num>", "?:")
    + r"(?:\s*(?:و|،|,|-)\s*" + _ONE.replace("?P<word>", "?:").replace("?P<num>", "?:") + r")*"
)
_REQUEST_ITEM = re.compile(_ONE)


_INDIC_DIGITS: dict[int, str | int | None] = {0x0660 + digit: str(digit) for digit in range(10)}


def _number(value: str) -> int:
    return int(value.translate(_INDIC_DIGITS))


def units_named_in(text: str) -> set[int]:
    """Every unit a chunk's text names as a header."""
    units: set[int] = set()
    for match in _HEADER.finditer(text):
        units.add(_ORDINAL_VALUE[match["word"]] if match["word"] else _number(match["num"]))
    for match in _ENGLISH_HEADER.finditer(text):
        units.add(int(match["num"]))
    return {unit for unit in units if 1 <= unit <= 30}


def label_units(texts: list[str]) -> list[int | None]:
    """The unit of each chunk (reading order), or None for front matter.

    A chunk naming exactly one unit belongs to it; a chunk naming several is a
    contents/overview page and does not change the current unit; a chunk
    naming none continues the unit before it. Everything before the first
    page of the lowest unit is front matter.
    """
    labels: list[int | None] = []
    current: int | None = None
    for text in texts:
        named = units_named_in(text)
        if len(named) == 1:
            current = next(iter(named))
        labels.append(current)
    # Content starts at the first page of the lowest unit: a contents page that
    # happens to name a single unit ("مشروع الوحدة الثالثة ... 280") sits
    # before it and is front matter too (production textbook, 2026-09-26).
    known = [label for label in labels if label is not None]
    if known:
        first = labels.index(min(known))
        labels[:first] = [None] * first
    return labels


def requested_units(instructions: str | None) -> set[int]:
    """Units a learner's free-text request asks for ("ركّز على الوحدة الثانية")."""
    if not instructions:
        return set()
    units: set[int] = set()
    for chain in _REQUEST_CHAIN.finditer(instructions):
        unit_word = _REQUEST_UNITS.match(chain.group(0))
        tail = chain.group(0)[unit_word.end() if unit_word else 0 :]
        for match in _REQUEST_ITEM.finditer(tail):
            units.add(_ORDINAL_VALUE[match["word"]] if match["word"] else _number(match["num"]))
    for match in _ENGLISH_HEADER.finditer(instructions):
        units.add(int(match["num"]))
    return {unit for unit in units if 1 <= unit <= 30}


def unit_label(unit: int, language: str = "ar") -> str:
    """Human name of a unit for titles and task names."""
    if language == "en":
        return f"Unit {unit}"
    names = {value: word for word, value in reversed(_ORDINALS) if not word.endswith("و")}
    word = names.get(unit)
    return f"{_UNIT_WORD} {word}" if word else f"{_UNIT_WORD} {unit}"
