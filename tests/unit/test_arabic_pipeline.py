"""Arabic document pipeline: extract -> chunk -> indexable text.

Baraq is Arabic-first, and the failure modes here are quiet ones. Arabic has
no casing, its sentence boundaries use different punctuation, and a chunker
tuned on Latin text can split mid-word or drop diacritics without raising
anything -- the job still completes, the embedding still succeeds, and the
retrieved passage simply no longer contains the fact the learner asked about.

These run the real extractor and the real chunker over real Arabic bytes.
They stop at the point a vector store is required: similarity search needs
PostgreSQL + pgvector, so that half is an RC gate rather than something
asserted here.
"""

from __future__ import annotations

from app.rag.chunker import ArabicAwareChunker
from app.rag.extractors import DocumentExtractor, ExtractedDocument

#: One unique, checkable fact. Nothing about it can be answered from general
#: knowledge, so retrieving it is evidence of retrieval rather than recall.
ARABIC_FACT = "يتكون بروتوكول برّاق التجريبي من سبع مراحل للتحقق."

ARABIC_DOCUMENT = f"""مقدمة في بروتوكولات التحقق

تستخدم الأنظمة التعليمية الحديثة آليات متعددة للتحقق من سلامة المحتوى.
{ARABIC_FACT}
وتعمل هذه المراحل بشكل متسلسل لضمان دقة النتيجة النهائية.

الفصل الثاني: التطبيق العملي

يمكن تطبيق البروتوكول على المصادر النصية والصوتية على حد سواء.
"""


def _extract(
    content: bytes, filename: str = "arabic.txt", mime: str = "text/plain"
) -> ExtractedDocument:
    return DocumentExtractor().extract(filename=filename, mime_type=mime, content=content)


def test_arabic_utf8_survives_extraction_intact() -> None:
    document = _extract(ARABIC_DOCUMENT.encode("utf-8"))

    assert ARABIC_FACT in document.full_text
    # Arabic-Indic and Arabic letters must not have been mangled into
    # replacement characters by a latin-1 fallback decode.
    assert "�" not in document.full_text
    assert "برّاق" in document.full_text, "the shadda-carrying brand name was altered"


def test_a_utf8_bom_does_not_corrupt_the_first_heading() -> None:
    """Windows editors write a BOM. Decoded as UTF-8 rather than UTF-8-SIG it
    becomes a zero-width character glued to the first word."""
    document = _extract("﻿".encode() + ARABIC_DOCUMENT.encode("utf-8"))

    assert document.full_text.lstrip().startswith("مقدمة")
    assert "﻿" not in document.full_text


def test_the_unique_fact_survives_chunking_in_one_piece() -> None:
    """A fact split across a chunk boundary is retrievable by neither half."""
    document = _extract(ARABIC_DOCUMENT.encode("utf-8"))
    chunks = ArabicAwareChunker(chunk_size=2200, overlap=300).chunk(document)

    assert chunks, "Arabic document produced no chunks"
    carrying = [chunk for chunk in chunks if ARABIC_FACT in chunk.text]
    assert carrying, (
        "the unique fact is in no single chunk; it was split across a boundary "
        "and is now retrievable by neither half"
    )


def test_a_small_chunk_size_still_never_splits_mid_word() -> None:
    """Under pressure the chunker must break on a boundary, not inside a word.

    Arabic is written without spaces between a word and its attached clitics,
    so a naive character-count split produces fragments that are not words in
    any sense and embed as noise.
    """
    document = _extract(ARABIC_DOCUMENT.encode("utf-8"))
    chunks = ArabicAwareChunker(chunk_size=120, overlap=20).chunk(document)

    assert len(chunks) > 1, "chunk_size=120 should have forced several chunks"
    for chunk in chunks:
        text = chunk.text.strip()
        assert text, "an empty chunk would embed as meaningless noise"
        # A chunk must not begin or end mid-token: the first and last
        # characters should not be a bare combining mark orphaned from its
        # base letter.
        assert not text[0].isspace()
        assert "�" not in text


def test_every_chunk_is_indexable_text() -> None:
    document = _extract(ARABIC_DOCUMENT.encode("utf-8"))
    chunks = ArabicAwareChunker(chunk_size=2200, overlap=300).chunk(document)

    for chunk in chunks:
        assert chunk.text.strip(), "a blank chunk still costs an embedding call"
        assert chunk.token_estimate > 0
        assert chunk.chunk_index >= 0
    indices = [chunk.chunk_index for chunk in chunks]
    assert indices == sorted(indices), "chunk order must be stable for citations"
    assert len(set(indices)) == len(indices), "duplicate chunk indices break citation"


def test_arabic_content_is_not_mistaken_for_an_unsupported_format() -> None:
    """The extractor dispatches on mime/suffix; Arabic content must not
    change that decision."""
    document = _extract(ARABIC_DOCUMENT.encode("utf-8"), filename="ملخص.txt")
    assert ARABIC_FACT in document.full_text


def test_windows1256_arabic_is_decoded_rather_than_rejected() -> None:
    """Older Arabic material is frequently cp1256, not UTF-8. Decoded as
    latin-1 it becomes mojibake that embeds as gibberish."""
    legacy = "بروتوكول التحقق".encode("cp1256")

    document = _extract(legacy)

    assert "�" not in document.full_text
    assert document.full_text.strip(), "cp1256 Arabic produced no text"
