from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.extractors import ExtractedDocument

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!؟؛:\n])\s+")


@dataclass(frozen=True, slots=True)
class TextChunk:
    chunk_index: int
    text: str
    page_number: int | None
    section_title: str | None
    token_estimate: int


def estimate_tokens(text: str) -> int:
    # Conservative language-independent estimate used only for budgeting.
    return max(1, len(text) // 3)


class ArabicAwareChunker:
    def __init__(self, *, chunk_size: int, overlap: int) -> None:
        if overlap >= chunk_size:
            raise ValueError("overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, document: ExtractedDocument) -> list[TextChunk]:
        output: list[TextChunk] = []
        index = 0
        carry = ""
        for page in document.pages:
            normalized = self._normalize(page.text)
            if not normalized:
                continue
            units = [unit.strip() for unit in _SENTENCE_BOUNDARY.split(normalized) if unit.strip()]
            current = carry
            for unit in units:
                candidate = f"{current} {unit}".strip()
                if len(candidate) <= self.chunk_size:
                    current = candidate
                    continue
                if current:
                    output.append(
                        TextChunk(
                            chunk_index=index,
                            text=current,
                            page_number=page.page_number,
                            section_title=page.section_title,
                            token_estimate=estimate_tokens(current),
                        )
                    )
                    index += 1
                    current = self._tail(current) + " " + unit
                else:
                    # Very long sentence or unbroken block.
                    for part in self._split_long(unit):
                        output.append(
                            TextChunk(
                                chunk_index=index,
                                text=part,
                                page_number=page.page_number,
                                section_title=page.section_title,
                                token_estimate=estimate_tokens(part),
                            )
                        )
                        index += 1
                    current = ""
            if current:
                output.append(
                    TextChunk(
                        chunk_index=index,
                        text=current,
                        page_number=page.page_number,
                        section_title=page.section_title,
                        token_estimate=estimate_tokens(current),
                    )
                )
                index += 1
                carry = self._tail(current)
            else:
                carry = ""
        # De-duplicate exact overlap-only chunks while preserving order.
        deduped: list[TextChunk] = []
        seen: set[str] = set()
        for item in output:
            key = item.text.strip()
            if key and key not in seen:
                deduped.append(
                    TextChunk(
                        chunk_index=len(deduped),
                        text=key,
                        page_number=item.page_number,
                        section_title=item.section_title,
                        token_estimate=item.token_estimate,
                    )
                )
                seen.add(key)
        return deduped

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\x00", " ").replace("\r\n", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _tail(self, text: str) -> str:
        return text[-self.overlap :] if self.overlap else ""

    def _split_long(self, text: str) -> list[str]:
        step = self.chunk_size - self.overlap
        return [text[start : start + self.chunk_size] for start in range(0, len(text), step)]
