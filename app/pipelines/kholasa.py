from __future__ import annotations

import json

from app.core.errors import ValidationFailure
from app.core.profanity import redact_model_list, redact_model_text
from app.core.security_flags import suspicious_source_flags
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult, require_project_id
from app.pipelines.student_text import clean_student_text
from app.prompts.registry import get_prompt_registry
from app.rag.grounding import ClaimEvidenceValidator
from app.rag.retriever import RAGRetriever
from app.schemas.kholasa import KholasaRequest, KholasaResult
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config


class KholasaPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.SELECTED_SOURCES_ONLY
    version = "2"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = KholasaRequest.model_validate(context.job.request_payload)
        project_id = require_project_id(context.job)
        for source_id in request.source_ids:
            await context.ingestion.ensure_ingested(
                source_id=source_id,
                user_id=context.job.user_id,
                project_id=project_id,
                expected_content_sha256=context.job.source_versions.get(source_id),
            )
        query = (
            "، ".join(request.focus_topics) or "الموضوعات الأساسية والتعريفات والقوانين والنتائج"
        )
        retriever = RAGRetriever(
            session=context.session,
            embeddings=context.ingestion.embeddings,
        )
        if request.focus_topics:
            rag = await retriever.retrieve(
                user_id=context.job.user_id,
                project_id=project_id,
                source_ids=request.source_ids,
                source_versions=context.job.source_versions,
                query=query,
                routing_key=f"{context.job.id}:kholasa:rag",
            )
        else:
            # The whole source is in scope: an even sample of it, not the
            # chunks nearest a generic stand-in query (that surfaced a
            # textbook's cover, committee and table of contents).
            rag = await retriever.retrieve_across(
                user_id=context.job.user_id,
                project_id=project_id,
                source_ids=request.source_ids,
                source_versions=context.job.source_versions,
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
        context.job.prompt_checksum = prompt.checksum
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
        # Blueprint 02_AI_PLATFORM.md §3.4: a summary never returns raw
        # profanity to the user without educational necessity.
        result, profanity_redacted = redact_model_text(
            result,
            text_fields=("title", "executive_summary", "detailed_summary"),
            list_fields=("key_points", "review_questions", "limitations"),
        )
        flashcards, flashcards_redacted = redact_model_list(
            result.flashcards, text_fields=("front", "back")
        )
        if flashcards_redacted:
            result = result.model_copy(update={"flashcards": flashcards})
            profanity_redacted = True
        evidence_texts = [citation.excerpt for citation in rag.citations]
        grounding_scores: list[float] = []
        # A flashcard its cited evidence does not support is dropped rather
        # than failing the whole summary (production 2026-09-25); the summary
        # itself is still checked against all of the evidence below.
        supported_cards = []
        for card in result.flashcards:
            try:
                score = ClaimEvidenceValidator.validate(
                    claim=f"{card.front} {card.back}",
                    source_references=card.source_references,
                    evidence_texts=evidence_texts,
                ).score
            except ValidationFailure:
                continue
            grounding_scores.append(score)
            supported_cards.append(card)
        if len(supported_cards) != len(result.flashcards):
            result = result.model_copy(update={"flashcards": supported_cards})
        summary_score = ClaimEvidenceValidator.validate(
            claim=" ".join([result.executive_summary, *result.key_points]),
            source_references=list(range(1, len(evidence_texts) + 1)),
            evidence_texts=evidence_texts,
        ).score
        groundedness = (sum(grounding_scores) + summary_score) / (len(grounding_scores) + 1)
        result = clean_student_text(result.model_copy(update={"citations": rag.citations}))
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=rag.citations,
            provider_result=provider_result,
            quality_score=groundedness,
            groundedness_score=groundedness,
            warnings=(["suspicious_source_content"] if rag.suspicious_source_detected else [])
            + (["profanity_redacted"] if profanity_redacted else []),
            security_flags=suspicious_source_flags(request.source_ids)
            if rag.suspicious_source_detected
            else [],
        )
