from __future__ import annotations

import json

from app.core.errors import ValidationFailure
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.rag.embeddings import EmbeddingService
from app.rag.retriever import RAGRetriever
from app.schemas.fahes import FahesRequest, FahesResult
from app.services.routing_config import get_routing_config


class FahesPipeline(AIPipeline):
    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = FahesRequest.model_validate(context.job.request_payload)
        for source_id in request.source_ids:
            await context.ingestion.ensure_ingested(
                source_id=source_id, user_id=context.job.user_id
            )

        query = request.topic or "المفاهيم الأساسية والقوانين والتعريفات والنقاط التي تقيس الفهم"
        retriever = RAGRetriever(
            session=context.session,
            embeddings=context.ingestion.embeddings,
        )
        rag = await retriever.retrieve(
            user_id=context.job.user_id,
            source_ids=request.source_ids,
            query=query,
            routing_key=f"{context.job.id}:fahes:rag",
        )
        if not rag.text:
            raise ValidationFailure(
                "No sufficiently relevant source context was found",
                code="insufficient_source_context",
            )

        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "fahes_generate_quiz")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        user_input = prompt.render_user(
            task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            source_context=rag.text,
        )
        provider_result = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=FahesResult,
        )
        result = FahesResult.model_validate(provider_result.data)
        max_reference = len(rag.citations)
        valid_refs = 0
        total_refs = 0
        for question in result.questions:
            total_refs += len(question.source_references)
            valid_refs += sum(1 for ref in question.source_references if 1 <= ref <= max_reference)
            if any(ref < 1 or ref > max_reference for ref in question.source_references):
                raise ValidationFailure(
                    "A generated question references a non-existent source chunk",
                    code="invalid_source_reference",
                )
        result = result.model_copy(update={"citations": rag.citations})
        groundedness = valid_refs / total_refs if total_refs else 0.0
        quality = max(0.0, min(1.0, 0.95 - 0.04 * len(result.warnings)))
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=rag.citations,
            provider_result=provider_result,
            quality_score=quality,
            groundedness_score=groundedness,
            warnings=["suspicious_source_content"] if rag.suspicious_source_detected else [],
        )
