from __future__ import annotations

import json
from datetime import timedelta

from app.core.errors import ValidationFailure
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.rag.retriever import RAGRetriever
from app.schemas.khota import KhotaRequest, KhotaResult
from app.services.routing_config import get_routing_config


class KhotaPipeline(AIPipeline):
    @staticmethod
    def _priority_scores(request: KhotaRequest) -> dict[str, float]:
        scores: dict[str, float] = {}
        for subject_id in request.subject_ids:
            score = 1.0
            exam = request.exam_dates.get(subject_id)
            if exam:
                days = max(0, (exam - request.start_date).days)
                score += max(0.0, (30 - min(days, 30)) / 10)
            weak_matches = sum(1 for topic in request.weak_topics if subject_id in topic)
            score += weak_matches * 0.5
            scores[subject_id] = round(score, 2)
        return scores

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = KhotaRequest.model_validate(context.job.request_payload)
        learner = await context.backend.get_learner_context(user_id=context.job.user_id)
        source_context = ""
        citations = []
        suspicious = False
        if request.source_ids:
            for source_id in request.source_ids:
                await context.ingestion.ensure_ingested(
                    source_id=source_id, user_id=context.job.user_id
                )
            retriever = RAGRetriever(
                session=context.session,
                embeddings=context.ingestion.embeddings,
            )
            rag = await retriever.retrieve(
                user_id=context.job.user_id,
                source_ids=request.source_ids,
                query="الموضوعات والوحدات التي يجب توزيعها في خطة دراسية",
                routing_key=f"{context.job.id}:khota:rag",
            )
            source_context = rag.text
            citations = rag.citations
            suspicious = rag.suspicious_source_detected

        priority_scores = self._priority_scores(request)
        constraints = {
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "daily_available_minutes": request.daily_available_minutes,
            "excluded_dates": [item.isoformat() for item in request.excluded_dates],
            "preferred_session_minutes": request.preferred_session_minutes,
            "learner_profile": learner.model_dump(mode="json"),
            "rule": "Do not exceed daily_available_minutes and do not schedule excluded dates.",
        }
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "khota_generate_plan")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        user_input = prompt.render_user(
            task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            backend_constraints=json.dumps(constraints, ensure_ascii=False),
            priority_scores=json.dumps(priority_scores, ensure_ascii=False),
            source_context=source_context or "لا يوجد مصدر مرفق؛ اعتمد على القيود والمواد فقط.",
        )
        provider_result = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=KhotaResult,
        )
        result = KhotaResult.model_validate(provider_result.data)
        excluded = set(request.excluded_dates)
        seen_dates = set()
        for day in result.plan_days:
            if day.date < request.start_date or day.date > request.end_date:
                raise ValidationFailure("Plan contains a date outside the requested range")
            if day.date in excluded:
                raise ValidationFailure("Plan contains an excluded date")
            if day.total_minutes > request.daily_available_minutes:
                raise ValidationFailure("Plan exceeds the learner daily time limit")
            if day.date in seen_dates:
                raise ValidationFailure("Plan contains duplicate days")
            seen_dates.add(day.date)
        result = result.model_copy(update={"citations": citations})
        coverage_days = max(1, (request.end_date - request.start_date).days + 1 - len(excluded))
        quality = min(1.0, len(result.plan_days) / coverage_days)
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=citations,
            provider_result=provider_result,
            quality_score=quality,
            groundedness_score=1.0 if not source_context else 0.9,
            warnings=["suspicious_source_content"] if suspicious else [],
        )
