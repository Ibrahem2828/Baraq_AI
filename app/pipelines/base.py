from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_job import AIJob
from app.providers.base import ProviderResult
from app.schemas.common import Citation
from app.services.backend_client import BackendClient
from app.services.generation import StructuredGenerationService
from app.services.source_ingestion import SourceIngestionService


@dataclass(slots=True)
class PipelineResult:
    result_json: dict[str, Any]
    citations: list[Citation]
    provider_result: ProviderResult
    quality_score: float | None = None
    groundedness_score: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PipelineContext:
    session: AsyncSession
    job: AIJob
    backend: BackendClient
    generation: StructuredGenerationService
    ingestion: SourceIngestionService


class AIPipeline(ABC):
    @abstractmethod
    async def execute(self, context: PipelineContext) -> PipelineResult: ...
