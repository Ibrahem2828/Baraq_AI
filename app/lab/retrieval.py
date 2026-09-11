from __future__ import annotations

import math
import re
from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ValidationFailure
from app.lab.models import LabChunk
from app.schemas.common import Citation

_TOKEN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOP = frozenset(
    {"the", "and", "for", "with", "from", "الى", "على", "في", "من", "عن", "هذا", "هذه"}
)


def _tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in _TOKEN.findall(text)
        if len(token) > 2 and token.casefold() not in _STOP
    ]


@dataclass(frozen=True, slots=True)
class LocalEvidence:
    evidence_id: str
    chunk: LabChunk
    lexical_score: float

    def citation(self) -> Citation:
        return Citation(
            evidence_id=UUID(self.chunk.chunk_id),
            source_id=self.chunk.source_id,
            content_sha256=self.chunk.content_sha256,
            chunk_id=UUID(self.chunk.chunk_id),
            page_number=self.chunk.page_number,
            section_title=self.chunk.section_title,
            excerpt=self.chunk.text[:1200],
            relevance_score=self.lexical_score,
            lexical_score=self.lexical_score,
            rerank_score=self.lexical_score,
        )


class LocalLexicalRetriever:
    """BM25-style local retrieval with strict source-id boundaries."""

    def retrieve(self, *, chunks: list[LabChunk], query: str, limit: int) -> list[LocalEvidence]:
        if not chunks:
            raise ValidationFailure("No processed source was selected", code="lab_no_source")
        query_terms = set(_tokens(query))
        if not query_terms:
            return [
                LocalEvidence(f"E{index}", chunk, 1.0)
                for index, chunk in enumerate(chunks[:limit], 1)
            ]
        document_frequency = {
            term: sum(term in set(_tokens(chunk.text)) for chunk in chunks) for term in query_terms
        }
        total = len(chunks)
        scored: list[tuple[float, LabChunk]] = []
        for chunk in chunks:
            terms = _tokens(chunk.text)
            if not terms:
                continue
            score = 0.0
            for term in query_terms:
                frequency = terms.count(term)
                if frequency:
                    score += (1 + math.log(frequency)) * math.log(
                        (total + 1) / (document_frequency[term] + 1) + 1
                    )
            if score:
                scored.append((score, chunk))
        if not scored:
            raise ValidationFailure(
                "Insufficient evidence in the selected source",
                code="insufficient_evidence",
            )
        maximum = max(score for score, _ in scored)
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            LocalEvidence(f"E{index}", chunk, round(score / maximum, 6))
            for index, (score, chunk) in enumerate(scored[:limit], 1)
        ]
