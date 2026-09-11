from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Protocol, TypeVar, cast

_ARABIC_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]")
_ARABIC_ALEF = "\u0627"
_TOKEN = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)


class RerankableChunk(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def section_title(self) -> str | None: ...

    @property
    def score(self) -> float: ...


ChunkT = TypeVar("ChunkT", bound=RerankableChunk)


def _normalize(text: str) -> str:
    text = _ARABIC_DIACRITICS.sub("", text.lower())
    return (
        text.replace("أ", _ARABIC_ALEF)
        .replace("إ", _ARABIC_ALEF)
        .replace("آ", _ARABIC_ALEF)
        .replace("ى", "ي")
        .replace("ؤ", "و")
        .replace("ئ", "ي")
    )


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(_normalize(text)) if len(token) > 1}


class HybridReranker:
    """Low-latency deterministic reranker.

    Blends pgvector semantic similarity with Arabic-aware lexical overlap and
    modest section-title coverage. It remains local so retrieval keeps working
    without an extra external reranking provider.
    """

    def rerank(self, *, query: str, chunks: list[ChunkT], limit: int) -> list[ChunkT]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return chunks[:limit]

        rescored: list[ChunkT] = []
        for chunk in chunks:
            body_tokens = _tokens(chunk.text)
            title_tokens = _tokens(chunk.section_title or "")
            lexical = len(query_tokens & body_tokens) / max(1, len(query_tokens))
            title_match = len(query_tokens & title_tokens) / max(1, len(query_tokens))
            final_score = min(
                1.0,
                max(0.0, (0.76 * chunk.score) + (0.18 * lexical) + (0.06 * title_match)),
            )
            # Retrieval records are immutable dataclasses.  The protocol keeps
            # the reranker reusable, while this cast expresses that concrete
            # runtime requirement to the type checker.
            updates: dict[str, Any] = {"score": round(final_score, 6)}
            if hasattr(chunk, "lexical_score"):
                updates["lexical_score"] = round(lexical, 6)
            if hasattr(chunk, "rerank_score"):
                updates["rerank_score"] = round(final_score, 6)
            rescored.append(cast(ChunkT, replace(cast(Any, chunk), **updates)))

        return sorted(rescored, key=lambda item: item.score, reverse=True)[:limit]
