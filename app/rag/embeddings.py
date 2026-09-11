from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.providers.capabilities import Capability
from app.providers.model_aliases import embedding_model_for
from app.providers.router import ProviderRouter
from app.services.provider_budget import ProviderBudgetService


class EmbeddingService:
    def __init__(self, router: ProviderRouter, *, session: AsyncSession) -> None:
        self.router = router
        self.settings = get_settings()
        self.budget = ProviderBudgetService(session)

    async def embed_documents(self, texts: list[str], routing_key: str) -> list[list[float]]:
        candidates = await self.router.candidates_for_capability(Capability.EMBEDDINGS, routing_key)
        last_error: Exception | None = None
        for candidate in candidates:
            if candidate.instance is None:
                continue
            if not await self.budget.has_budget(candidate.account_id):
                continue
            model = embedding_model_for(self.settings, candidate.provider)
            try:
                vectors: list[list[float]] = []
                for start in range(0, len(texts), 100):
                    result = await candidate.instance.embed(
                        model=model,
                        texts=texts[start : start + 100],
                    )
                    vectors.extend(result.vectors)
                    await self.budget.record(result)
                await self.router.circuit.record_success(candidate.account_id)
                return vectors
            except Exception as exc:
                last_error = exc
                await self.router.circuit.record_failure(candidate.account_id)
        if last_error:
            raise last_error
        return []

    async def embed_query(self, query: str, routing_key: str) -> list[float]:
        vectors = await self.embed_documents([query], routing_key)
        return vectors[0]
