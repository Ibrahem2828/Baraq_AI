"""The retriever's similarity floor can be overridden per call."""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.config import get_settings
from app.rag.retriever import RAGRetriever


class _Embeddings:
    async def embed_query(self, query: str, routing_key: str) -> list[float]:
        return [0.0, 1.0]


class _Repository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return []

    async def sample_across(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return []


def _retriever() -> tuple[RAGRetriever, _Repository]:
    retriever = RAGRetriever(session=cast(Any, object()), embeddings=cast(Any, _Embeddings()))
    repository = _Repository()
    retriever.repository = cast(Any, repository)
    return retriever, repository


async def _retrieve(retriever: RAGRetriever, **extra: Any) -> None:
    await retriever.retrieve(
        user_id="u",
        project_id="p",
        source_ids=["s"],
        source_versions={"s": "h"},
        query="q",
        routing_key="k",
        **extra,
    )


@pytest.mark.asyncio
async def test_the_configured_floor_applies_by_default() -> None:
    retriever, repository = _retriever()
    await _retrieve(retriever)
    assert repository.calls[0]["min_similarity"] == get_settings().rag_min_similarity


@pytest.mark.asyncio
async def test_an_explicit_zero_floor_reaches_the_repository() -> None:
    retriever, repository = _retriever()
    await _retrieve(retriever, min_similarity=0.0)
    assert repository.calls[0]["min_similarity"] == 0.0


@pytest.mark.asyncio
async def test_a_whole_source_sample_fits_the_context_budget() -> None:
    """_context() stops at the character budget; sampling more chunks than fit
    would silently drop the end of the source."""
    retriever, repository = _retriever()
    await retriever.retrieve_across(
        user_id="u", project_id="p", source_ids=["s"], source_versions={"s": "h"}
    )
    settings = get_settings()
    limit = repository.calls[0]["limit"]
    assert 1 <= limit <= settings.rag_top_k
    assert limit * (settings.rag_chunk_size_chars + 200) <= settings.rag_max_context_chars
