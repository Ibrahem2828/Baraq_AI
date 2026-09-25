from __future__ import annotations

import json

from app.core.errors import ValidationFailure
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.rag.grounding import validate_topic_references
from app.schemas.rasheed import RasheedRequest, RasheedResult
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config


class RasheedPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.AUTHORITATIVE_DATA_ONLY
    version = "2"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        request = RasheedRequest.model_validate(context.job.request_payload)
        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "rasheed_recommend")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        context.job.prompt_checksum = prompt.checksum
        authoritative_metrics = [item for item in request.metrics if item.authoritative]
        if not authoritative_metrics:
            raise ValidationFailure(
                "Rasheed requires authoritative metrics",
                code="missing_authoritative_data",
            )
        authority_data = {
            "metrics": [item.model_dump(mode="json") for item in authoritative_metrics],
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
        # Blueprint 02_AI_PLATFORM.md §11: every pipeline needs a
        # grounding/source check, not just a schema check. Rasheed has no RAG
        # excerpts to lexically match (ClaimEvidenceValidator doesn't apply),
        # but related_topics is a closed set -- a recommendation citing a
        # topic absent from the learner's own authoritative data is a
        # fabricated reference, the same failure mode grounding checks exist
        # to catch elsewhere.
        known_topics = {item.topic.strip().casefold() for item in request.topic_performance}
        for recommendation in result.recommendations:
            validate_topic_references(
                known_topics=known_topics, cited_topics=recommendation.related_topics
            )
        # Strengths and weaknesses are topics too, and Khota plans study
        # sessions on the weaknesses. With no per-topic data the model wrote
        # sentences about the missing data there ("no quiz attempts were
        # recorded"), which then became study tasks; keep only real topics.
        result = result.model_copy(
            update={
                "strengths": _known_only(result.strengths, known_topics),
                "weaknesses": _known_only(result.weaknesses, known_topics),
            }
        )
        data_strength = min(1.0, (len(authoritative_metrics) + len(request.topic_performance)) / 10)
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=[],
            provider_result=provider_result,
            quality_score=data_strength,
            groundedness_score=data_strength,
        )


def _known_only(topics: list[str], known_topics: set[str]) -> list[str]:
    """Items naming one of the learner's own topics (allowing "Topic X" for "X")."""
    kept: list[str] = []
    for topic in topics:
        folded = topic.strip().casefold()
        if folded and any(known and (known in folded or folded in known) for known in known_topics):
            kept.append(topic)
    return kept
