from __future__ import annotations

import json

from app.core.errors import ValidationFailure
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.rag.retriever import RAGRetriever
from app.schemas.kholasa import KholasaRequest, KholasaResult
from app.services.routing_config import get_routing_config


class KholasaPipeline(AIPipeline):
    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = KholasaRequest.model_validate(context.job.request_payload)
        for source_id in request.source_ids:
            await context.ingestion.ensure_ingested(
                source_id=source_id, user_id=context.job.user_id
            )
        query = "، ".join(request.focus_topics) or "الموضوعات الأساسية والتعريفات والقوانين والنتائج"
        retriever = RAGRetriever(
            session=context.session,
            embeddings=context.ingestion.embeddings,
        )
        rag = await retriever.retrieve(
            user_id=context.job.user_id,
            source_ids=request.source_ids,
            query=query,
            routing_key=f"{context.job.id}:kholasa:rag",
        )
        if not rag.text:
            raise ValidationFailure(
                "No sufficiently relevant source context was found",
                code="insufficient_source_context",
            )
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "kholasa_summarize")
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
            output_model=KholasaResult,
        )
        result = KholasaResult.model_validate(provider_result.data)
        max_reference = len(rag.citations)
        for card in result.flashcards:
            if any(ref < 1 or ref > max_reference for ref in card.source_references):
                raise ValidationFailure(
                    "A flashcard references a non-existent source chunk",
                    code="invalid_source_reference",
                )
        result = result.model_copy(update={"citations": rag.citations})
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=rag.citations,
            provider_result=provider_result,
            quality_score=max(0.0, 0.96 - 0.04 * len(result.limitations)),
            groundedness_score=0.95,
            warnings=["suspicious_source_content"] if rag.suspicious_source_detected else [],
        )
