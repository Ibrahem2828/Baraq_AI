from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.providers.candidate import ProviderCandidate
from app.providers.capabilities import Capability
from app.providers.model_aliases import embedding_model_for
from app.providers.router import ProviderRouter
from app.services.cost import estimate_tokens, get_cost_calculator
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
            try:
                vectors = await self._embed_within_budget(candidate, texts)
            except Exception as exc:
                last_error = exc
                await self.router.circuit.record_failure(candidate.account_id)
                continue
            if vectors is None:
                # Budget ran out partway through this candidate's batches --
                # try the next fallback candidate instead of returning a
                # partial embedding set.
                continue
            await self.router.circuit.record_success(candidate.account_id)
            return vectors
        if last_error:
            raise last_error
        return []

    async def _embed_within_budget(
        self, candidate: ProviderCandidate, texts: list[str]
    ) -> list[list[float]] | None:
        """Returns None if the budget ran out before all batches finished
        (caller should try the next candidate), raises on a real provider
        failure, otherwise returns the full embedding list."""
        assert candidate.instance is not None
        model = embedding_model_for(self.settings, candidate.provider)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 100):
            batch = texts[start : start + 100]
            # Exact input text is already known (nothing generated), so the
            # char/4 estimate here is only a placeholder ceiling for the
            # reservation -- commit_actual() records whatever the provider
            # actually reports/charges for this batch.
            estimated_tokens = sum(estimate_tokens(text) for text in batch)
            max_batch_cost = get_cost_calculator().embedding_cost(model, estimated_tokens)
            reservation = await self.budget.reserve(candidate.account_id, max_batch_cost)
            if not reservation.granted:
                return None
            try:
                result = await candidate.instance.embed(model=model, texts=batch)
            except Exception:
                await self.budget.release(reservation)
                raise
            vectors.extend(result.vectors)
            await self.budget.commit_actual(reservation, result)
        return vectors

    async def embed_query(self, query: str, routing_key: str) -> list[float]:
        vectors = await self.embed_documents([query], routing_key)
        return vectors[0]
