from __future__ import annotations

import json

from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.schemas.rasheed import RasheedRequest, RasheedResult
from app.services.routing_config import get_routing_config


class RasheedPipeline(AIPipeline):
    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = RasheedRequest.model_validate(context.job.request_payload)
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "rasheed_recommend")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        authority_data = {
            "metrics": [item.model_dump(mode="json") for item in request.metrics],
            "topic_performance": [
                item.model_dump(mode="json") for item in request.topic_performance
            ],
            "recent_actions": request.recent_actions,
        }
        user_input = prompt.render_user(
            task_parameters=json.dumps(
                {"learner_goal": request.learner_goal, "language": request.language},
                ensure_ascii=False,
            ),
            authority_data=json.dumps(authority_data, ensure_ascii=False),
        )
        provider_result = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=RasheedResult,
        )
        result = RasheedResult.model_validate(provider_result.data)
        data_strength = min(1.0, (len(request.metrics) + len(request.topic_performance)) / 10)
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=[],
            provider_result=provider_result,
            quality_score=0.9,
            groundedness_score=data_strength,
        )
