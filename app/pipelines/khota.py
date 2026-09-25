from __future__ import annotations

import json

from pydantic import ValidationError as PydanticValidationError

from app.core.errors import AppError, ValidationFailure
from app.core.security_flags import suspicious_source_flags
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult, require_project_id
from app.pipelines.learner_request import learner_instructions
from app.pipelines.student_text import clean_student_text, strip_source_labels
from app.prompts.registry import get_prompt_registry
from app.rag.grounding import ClaimEvidenceValidator
from app.rag.retriever import RAGRetriever
from app.rag.scope import scoped_context
from app.schemas.common import Citation
from app.schemas.khota import KhotaNarrative, KhotaOutline, KhotaRequest, KhotaResult
from app.services.khota_scheduler import build_plan_days, priority_scores, validate_hard_constraints
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config


class KhotaPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.AUTHORITATIVE_CONSTRAINTS
    version = "3"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = KhotaRequest.model_validate(context.job.request_payload)
        project_id = require_project_id(context.job)
        learner = await context.backend.get_learner_context(user_id=context.job.user_id)
        source_context = ""
        citations: list[Citation] = []
        suspicious = False
        topics: list[str] = []
        scope_units: set[int] = set()
        scope_warnings: list[str] = []
        if request.source_ids:
            for source_id in request.source_ids:
                await context.ingestion.ensure_ingested(
                    source_id=source_id,
                    user_id=context.job.user_id,
                    project_id=project_id,
                    expected_content_sha256=context.job.source_versions.get(source_id),
                )
            retriever = RAGRetriever(
                session=context.session,
                embeddings=context.ingestion.embeddings,
            )
            # The plan walks through the source (or the units the learner
            # asked for) in reading order: an even sample of it, never the
            # chunks nearest a generic query (the cover and contents).
            scope = await scoped_context(
                retriever,
                user_id=context.job.user_id,
                project_id=project_id,
                source_ids=request.source_ids,
                source_versions=context.job.source_versions,
                routing_key=f"{context.job.id}:khota:rag",
                focus=None,
                instructions=request.instructions,
                language=request.language,
            )
            rag = scope.rag
            scope_units = scope.units
            scope_warnings = list(scope.warnings)
            source_context = rag.text
            citations = rag.citations
            suspicious = rag.suspicious_source_detected
        instructions_block, instructions_dropped = learner_instructions(
            request.instructions, units=scope_units, language=request.language
        )
        if instructions_dropped:
            scope_warnings.append("learner_instructions_ignored")
        if source_context:
            topics = await self._source_topics(
                context, request, citations, source_context, instructions_block
            )

        # The schedule itself is deterministic: same request + topics always
        # produce the same days, minutes and priorities (spec section 17).
        # The LLM never sees this as something to author -- only to explain.
        scores = priority_scores(request)
        plan_days = build_plan_days(request, topics=topics)
        if not plan_days:
            raise ValidationFailure(
                "No study days remain after exclusions", code="khota_no_study_days"
            )
        try:
            validate_hard_constraints(request, plan_days)
        except ValueError as exc:
            raise ValidationFailure(str(exc), code="khota_constraint_violation") from exc

        constraints = {
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "daily_available_minutes": request.daily_available_minutes,
            "excluded_dates": [item.isoformat() for item in request.excluded_dates],
            "preferred_session_minutes": request.preferred_session_minutes,
            "learner_profile": learner.model_dump(mode="json"),
        }
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "khota_generate_plan")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        context.job.prompt_checksum = prompt.checksum
        user_input = prompt.render_user(
            task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            backend_constraints=json.dumps(constraints, ensure_ascii=False),
            priority_scores=json.dumps(scores, ensure_ascii=False),
            deterministic_plan=json.dumps(
                [day.model_dump(mode="json") for day in plan_days], ensure_ascii=False
            ),
            source_context=source_context or "لا يوجد مصدر مرفق؛ اعتمد على القيود والمواد فقط.",
            learner_instructions=instructions_block,
        )
        provider_result = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=KhotaNarrative,
        )
        narrative = clean_student_text(KhotaNarrative.model_validate(provider_result.data))
        result = KhotaResult(
            title=narrative.title,
            strategy_summary=narrative.strategy_summary,
            plan_days=plan_days,
            assumptions=narrative.assumptions,
            adaptation_rules=narrative.adaptation_rules,
            citations=citations,
        )
        excluded_count = len(request.excluded_dates)
        coverage_days = max(1, (request.end_date - request.start_date).days + 1 - excluded_count)
        quality = min(1.0, len(result.plan_days) / coverage_days)
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=citations,
            provider_result=provider_result,
            quality_score=quality,
            groundedness_score=(quality if source_context else None),
            warnings=scope_warnings + (["suspicious_source_content"] if suspicious else []),
            security_flags=suspicious_source_flags(request.source_ids) if suspicious else [],
        )

    async def _source_topics(
        self,
        context: PipelineContext,
        request: KhotaRequest,
        citations: list[Citation],
        source_context: str,
        instructions_block: str,
    ) -> list[str]:
        """The source's own topics, in reading order, each checked against its
        excerpt and named with its unit. Any failure falls back to the unit
        names the sample carries: a plan is never lost to this step."""
        units = [citation.section_title for citation in citations if citation.section_title]
        fallback = list(dict.fromkeys(units))
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get("khota_extract_topics")
        try:
            provider_result = await context.generation.generate(
                job=context.job,
                routing=routing,
                prompt=prompt,
                user_input=prompt.render_user(
                    task_parameters=json.dumps({"language": request.language}),
                    learner_instructions=instructions_block,
                    source_context=source_context,
                ),
                output_model=KhotaOutline,
            )
            outline = KhotaOutline.model_validate(provider_result.data)
        except (AppError, PydanticValidationError):
            return fallback
        evidence = [citation.excerpt for citation in citations]
        topics: list[str] = []
        for topic in outline.topics:
            if topic.source_reference > len(evidence):
                continue
            title = strip_source_labels(topic.title).strip()
            try:
                ClaimEvidenceValidator.validate(
                    claim=title,
                    source_references=[topic.source_reference],
                    evidence_texts=evidence,
                )
            except ValidationFailure:
                continue  # not supported by the excerpt it cites
            unit = citations[topic.source_reference - 1].section_title
            topics.append(f"{unit}: {title}" if unit and unit not in title else title)
        return list(dict.fromkeys(topics))[:30] or fallback
