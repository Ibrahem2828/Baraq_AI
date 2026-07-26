from __future__ import annotations

from app.core.config import get_settings
from app.providers.router import ProviderRouter


class EmbeddingService:
    def __init__(self, router: ProviderRouter) -> None:
        self.router = router
        self.settings = get_settings()

    async def embed_documents(self, texts: list[str], routing_key: str) -> list[list[float]]:
        candidates = await self.router.ordered_candidates(routing_key)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                vectors: list[list[float]] = []
                for start in range(0, len(texts), 100):
                    vectors.extend(
                        await candidate.provider.embed(
                            model=self.settings.openai_embedding_model,
                            texts=texts[start : start + 100],
                        )
                    )
                await self.router.circuit.record_success(candidate.account)
                return vectors
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                await self.router.circuit.record_failure(candidate.account)
        if last_error:
            raise last_error
        return []

    async def embed_query(self, query: str, routing_key: str) -> list[float]:
        vectors = await self.embed_documents([query], routing_key)
        return vectors[0]
