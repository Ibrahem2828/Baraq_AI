from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BackendEndpoints:
    """Single source of truth for Django internal AI integration paths."""

    source_manifest_template: str = "/api/internal/v1/ai/sources/{source_id}/manifest/"
    source_download_template: str = "/api/internal/v1/ai/sources/{source_id}/download/"
    collection_manifest_template: str = "/api/internal/v1/ai/collections/{collection_id}/manifest/"
    learner_context_template: str = "/api/internal/v1/ai/users/{user_id}/context/"
    job_webhook: str = "/api/internal/v1/ai/webhooks/jobs/"

    def source_manifest(self, source_id: str) -> str:
        return self.source_manifest_template.format(source_id=source_id)

    def source_download(self, source_id: str) -> str:
        return self.source_download_template.format(source_id=source_id)

    def collection_manifest(self, collection_id: str) -> str:
        return self.collection_manifest_template.format(collection_id=collection_id)

    def learner_context(self, user_id: str) -> str:
        return self.learner_context_template.format(user_id=user_id)


BACKEND_ENDPOINTS = BackendEndpoints()
