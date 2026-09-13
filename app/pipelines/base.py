from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailure
from app.models.ai_job import AIJob
from app.providers.base import ProviderResult
from app.schemas.common import Citation
from app.services.backend_client import BackendClient
from app.services.generation import StructuredGenerationService
from app.services.knowledge_policy import KnowledgePolicy
from app.services.source_ingestion import SourceIngestionService


@dataclass(slots=True)
class PipelineResult:
    result_json: dict[str, Any]
    citations: list[Citation]
    provider_result: ProviderResult
    quality_score: float | None = None
    groundedness_score: float | None = None
    warnings: list[str] = field(default_factory=list)
    security_flags: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PipelineContext:
    session: AsyncSession
    job: AIJob
    backend: BackendClient
    generation: StructuredGenerationService
    ingestion: SourceIngestionService


class AIPipeline(ABC):
    knowledge_policy: KnowledgePolicy
    version = "1"

    @abstractmethod
    async def execute(self, context: PipelineContext) -> PipelineResult: ...


def require_project_id(job: AIJob) -> str:
    """Reject historic/unscoped rows before any source or provider work."""
    if not job.project_id:
        raise ValidationFailure("Project scope is required", code="project_id_required")
    return job.project_id
