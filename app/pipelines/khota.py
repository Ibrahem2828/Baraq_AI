from __future__ import annotations

import json

from app.core.errors import ValidationFailure
from app.core.security_flags import suspicious_source_flags
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.rag.retriever import RAGRetriever
from app.schemas.khota import KhotaNarrative, KhotaRequest, KhotaResult
from app.services.khota_scheduler import build_plan_days, priority_scores, validate_hard_constraints
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config


class KhotaPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.AUTHORITATIVE_CONSTRAINTS
    version = "3"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = KhotaRequest.model_validate(context.job.request_payload)
        learner = await context.backend.get_learner_context(user_id=context.job.user_id)
        source_context = ""
        citations = []
        suspicious = False
        topics: list[str] = []
        if request.source_ids:
            for source_id in request.source_ids:
                await context.ingestion.ensure_ingested(
                    source_id=source_id,
                    user_id=context.job.user_id,
                    project_id=context.job.project_id,
                    expected_content_sha256=context.job.source_versions.get(source_id),
                )
            retriever = RAGRetriever(
                session=context.session,
                embeddings=context.ingestion.embeddings,
            )
            rag = await retriever.retrieve(
                user_id=context.job.user_id,
                project_id=context.job.project_id,
                source_ids=request.source_ids,
                source_versions=context.job.source_versions,
                query="الموضوعات والوحدات التي يجب توزيعها في خطة دراسية",
                routing_key=f"{context.job.id}:khota:rag",
            )
            source_context = rag.text
            citations = rag.citations
            suspicious = rag.suspicious_source_detected
            topics = [citation.section_title for citation in citations if citation.section_title]

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
        )
        provider_result = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=KhotaNarrative,
        )
        narrative = KhotaNarrative.model_validate(provider_result.data)
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
            warnings=["suspicious_source_content"] if suspicious else [],
            security_flags=suspicious_source_flags(request.source_ids) if suspicious else [],
        )
