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
from app.schemas.fahes import FahesRequest, FahesResult
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config


class FahesPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.SELECTED_SOURCES_ONLY
    version = "2"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = FahesRequest.model_validate(context.job.request_payload)
        project_id = require_project_id(context.job)
        for source_id in request.source_ids:
            await context.ingestion.ensure_ingested(
                source_id=source_id,
                user_id=context.job.user_id,
                project_id=project_id,
                expected_content_sha256=context.job.source_versions.get(source_id),
            )

        query = request.topic or "المفاهيم الأساسية والقوانين والتعريفات والنقاط التي تقيس الفهم"
        retriever = RAGRetriever(
            session=context.session,
            embeddings=context.ingestion.embeddings,
        )
        if request.topic:
            rag = await retriever.retrieve(
                user_id=context.job.user_id,
                project_id=project_id,
                source_ids=request.source_ids,
                source_versions=context.job.source_versions,
                query=query,
                routing_key=f"{context.job.id}:fahes:rag",
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
        prompt = get_prompt_registry().get(routing.prompt or "fahes_generate_quiz")
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
            output_model=FahesResult,
        )
        result = FahesResult.model_validate(provider_result.data)
        # Blueprint 02_AI_PLATFORM.md §3.4: educational output must not
        # generate profanity as questions/choices without educational
        # necessity + explicit policy allowance -- neither applies here, so
        # any hit is redacted unconditionally.
        result, title_redacted = redact_model_text(result, text_fields=("title", "description"))
        questions, questions_redacted = redact_model_list(
            result.questions, text_fields=("question", "explanation"), list_fields=("choices",)
        )
        profanity_redacted = title_redacted or questions_redacted
        if questions_redacted:
            result = result.model_copy(update={"questions": questions})
        evidence_texts = [citation.excerpt for citation in rag.citations]
        grounding_scores: list[float] = []
        # A question its cited evidence does not support is dropped, not the
        # whole quiz with it; only a quiz with no supported question fails.
        supported_questions = []
        first_failure: ValidationFailure | None = None
        for question in result.questions:
            claim = " ".join(
                [
                    question.question,
                    question.choices[question.correct_answer_index],
                    question.explanation,
                ]
            )
            try:
                score = ClaimEvidenceValidator.validate(
                    claim=claim,
                    source_references=question.source_references,
                    evidence_texts=evidence_texts,
                ).score
            except ValidationFailure as exc:
                first_failure = first_failure or exc
                continue
            grounding_scores.append(score)
            supported_questions.append(question)
        if not supported_questions and first_failure is not None:
            raise first_failure
        if len(supported_questions) != len(result.questions):
            result = result.model_copy(update={"questions": supported_questions})
        result = clean_student_text(result.model_copy(update={"citations": rag.citations}))
        groundedness = sum(grounding_scores) / len(grounding_scores) if grounding_scores else None
        quality = groundedness
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=rag.citations,
            provider_result=provider_result,
            quality_score=quality,
            groundedness_score=groundedness,
            warnings=(["suspicious_source_content"] if rag.suspicious_source_detected else [])
            + (["profanity_redacted"] if profanity_redacted else []),
            security_flags=suspicious_source_flags(request.source_ids)
            if rag.suspicious_source_detected
            else [],
        )
