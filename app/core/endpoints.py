from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendEndpoints:
    """Single source of truth for all Baraq backend integration paths."""

    source_manifest_template: str = "/api/internal/ai/v1/sources/{source_id}/manifest/"
    learner_context_template: str = "/api/internal/ai/v1/users/{user_id}/context/"
    credits_reserve: str = "/api/internal/ai/v1/credits/reserve/"
    credits_commit: str = "/api/internal/ai/v1/credits/commit/"
    credits_refund: str = "/api/internal/ai/v1/credits/refund/"
    materialize: str = "/api/internal/ai/v1/materialize/"

    def source_manifest(self, source_id: str, user_id: str) -> str:
        return self.source_manifest_template.format(source_id=source_id) + f"?user_id={user_id}"

    def learner_context(self, user_id: str) -> str:
        return self.learner_context_template.format(user_id=user_id)


BACKEND_ENDPOINTS = BackendEndpoints()
