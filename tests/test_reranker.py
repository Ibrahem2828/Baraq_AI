from __future__ import annotations

from dataclasses import dataclass

from app.rag.reranker import HybridReranker


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    score: float
    section_title: str | None = None


def test_hybrid_reranker_rewards_arabic_lexical_overlap() -> None:
    chunks = [
        Chunk("محتوى بعيد عن الموضوع", 0.80),
        Chunk("تعريف قانون نيوتن الثاني والقوة والتسارع", 0.70, "قوانين نيوتن"),
    ]
    result = HybridReranker().rerank(
        query="قانون نيوتن الثاني",
        chunks=chunks,
        limit=2,
    )
    assert "نيوتن" in result[0].text
    assert result[0].score > result[1].score
