"""Shared character application service using standalone Lab infrastructure.

This module deliberately depends on schemas, prompt registry, provider boundary
and grounding validators rather than HTTP handlers. Django/service adapters can
call the same `run_task` contract in a later integration round.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.errors import ValidationFailure
from app.lab.retrieval import LocalEvidence, LocalLexicalRetriever
from app.lab.storage import LabStorage
from app.lab.stt import LocalWhisperAdapter
from app.models.enums import TaskType
from app.prompts.registry import get_prompt_registry
from app.providers.base import LLMProvider, ProviderResult
from app.providers.model_aliases import model_for_tier
from app.rag.grounding import ClaimEvidenceValidator
from app.schemas.common import StrictModel
from app.schemas.fahes import FahesRequest, FahesResult
from app.schemas.kholasa import KholasaRequest, KholasaResult
from app.schemas.khota import KhotaRequest, KhotaResult
from app.schemas.rasheed import RasheedRequest, RasheedResult
from app.schemas.sada import SadaCleanupResult, SadaRequest, SadaResult, TranscriptSegment
from app.services.khota_scheduler import build_plan_days
from app.services.routing_config import get_routing_config


@dataclass(frozen=True, slots=True)
class LabRunResult:
    result: dict[str, Any]
    evidence: list[dict[str, Any]]
    validation: dict[str, Any]
    provider: dict[str, Any]


class BaraqAIApplication:
    """Application boundary used by Lab now and service adapters later."""

    def __init__(
        self, *, settings: Settings, storage: LabStorage, provider: LLMProvider
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.provider = provider
        self.retriever = LocalLexicalRetriever()
        self.stt = LocalWhisperAdapter(
            model_name=settings.sada_local_whisper_model,
            language=settings.sada_language,
        )

    async def run_task(
        self,
        *,
        workspace_id: str,
        task_type: TaskType,
        input: dict[str, Any],
        thinking: bool | None = None,
    ) -> LabRunResult:
        if task_type == TaskType.KHOTA_GENERATE_PLAN:
            return self._run_khota(workspace_id=workspace_id, input=input)
        if task_type == TaskType.RASHEED_RECOMMENDATIONS:
            return await self._run_rasheed(
                workspace_id=workspace_id, input=input, thinking=thinking
            )
        if task_type == TaskType.FAHES_GENERATE_QUIZ:
            return await self._run_fahes(workspace_id=workspace_id, input=input, thinking=thinking)
        if task_type == TaskType.KHOLASA_GENERATE_SUMMARY:
            return await self._run_kholasa(
                workspace_id=workspace_id, input=input, thinking=thinking
            )
        if task_type == TaskType.SADA_TRANSCRIBE_AUDIO:
            return await self._run_sada(workspace_id=workspace_id, input=input, thinking=thinking)
        raise ValidationFailure(
            "This task is not available through the standalone text Lab",
            code="lab_task_unavailable",
        )

    def _evidence(
        self, *, workspace_id: str, source_ids: list[str], query: str
    ) -> list[LocalEvidence]:
        chunks = self.storage.list_chunks(workspace_id=workspace_id, source_ids=source_ids)
        return self.retriever.retrieve(
            chunks=chunks, query=query, limit=self.settings.rag_rerank_top_k
        )

    @staticmethod
    def _source_context(evidence: list[LocalEvidence]) -> str:
        blocks: list[str] = []
        for index, item in enumerate(evidence, 1):
            chunk = item.chunk
            location = f"page={chunk.page_number}" if chunk.page_number else "section"
            if chunk.section_title:
                location += f" {chunk.section_title}"
            blocks.append(f"[S{index}] {location}\n{chunk.text}")
        return "\n\n".join(blocks)

    @staticmethod
    def _evidence_dump(evidence: list[LocalEvidence]) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": item.evidence_id,
                "citation": item.citation().model_dump(mode="json"),
                "score": item.lexical_score,
            }
            for item in evidence
        ]

    async def _generate(
        self,
        *,
        task_type: TaskType,
        user_input: str,
        output_model: type[StrictModel],
        thinking: bool | None,
    ) -> ProviderResult:
        routing = get_routing_config().get(task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or task_type.value)
        # Lab drives one directly-configured LLMProvider (mock/replay/gemini/
        # openai per PROVIDER_MODE) rather than the service's multi-candidate
        # router -- it still resolves the model the same provider-neutral
        # way (app/providers/model_aliases.py), never a hard-coded name.
        preferred_tier = (
            routing.candidates[0].quality_tier.value if routing.candidates else "balanced"
        )
        model = model_for_tier(self.settings, self.provider.provider_family, preferred_tier)
        reasoning_effort = routing.reasoning_effort
        if thinking is True:
            reasoning_effort = "high"
        elif thinking is False:
            reasoning_effort = "low"
        generated = await self.provider.generate_structured(
            model=model,
            instructions=prompt.system_prompt,
            user_input=user_input,
            output_model=output_model,
            schema_name=prompt.name,
            max_output_tokens=min(routing.max_output_tokens, self.settings.lab_max_output_tokens),
            reasoning_effort=reasoning_effort,
            metadata={"task_type": task_type.value, "runtime_mode": "lab"},
        )
        generated.metadata["prompt_trace"] = {
            "name": prompt.name,
            "version": prompt.version,
            "checksum": prompt.checksum,
            "reasoning_effort": reasoning_effort,
        }
        return generated

    async def _run_fahes(
        self, *, workspace_id: str, input: dict[str, Any], thinking: bool | None
    ) -> LabRunResult:
        request = FahesRequest.model_validate(input)
        # With no explicit topic, retrieval must remain language-neutral.  An
        # empty query intentionally returns the selected source's first
        # usable chunks rather than failing Arabic defaults against English
        # material (or the reverse).
        query = request.topic or ""
        evidence = self._evidence(
            workspace_id=workspace_id, source_ids=request.source_ids, query=query
        )
        prompt = get_prompt_registry().get("fahes_generate_quiz")
        candidate = await self._generate(
            task_type=TaskType.FAHES_GENERATE_QUIZ,
            user_input=prompt.render_user(
                task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                source_context=self._source_context(evidence),
            ),
            output_model=FahesResult,
            thinking=thinking,
        )
        result = FahesResult.model_validate(candidate.data)
        citations = [item.citation() for item in evidence]
        scores = []
        for question in result.questions:
            claim = " ".join(
                [
                    question.question,
                    question.choices[question.correct_answer_index],
                    question.explanation,
                ]
            )
            scores.append(
                ClaimEvidenceValidator.validate(
                    claim=claim,
                    source_references=question.source_references,
                    evidence_texts=[citation.excerpt for citation in citations],
                ).score
            )
        verified = result.model_copy(update={"citations": citations})
        return self._result(
            result=verified,
            evidence=evidence,
            provider=candidate,
            validation={"status": "valid", "groundedness": sum(scores) / len(scores)},
        )

    async def _run_kholasa(
        self, *, workspace_id: str, input: dict[str, Any], thinking: bool | None
    ) -> LabRunResult:
        request = KholasaRequest.model_validate(input)
        query = " ".join(request.focus_topics)
        evidence = self._evidence(
            workspace_id=workspace_id, source_ids=request.source_ids, query=query
        )
        prompt = get_prompt_registry().get("kholasa_summarize")
        candidate = await self._generate(
            task_type=TaskType.KHOLASA_GENERATE_SUMMARY,
            user_input=prompt.render_user(
                task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                source_context=self._source_context(evidence),
            ),
            output_model=KholasaResult,
            thinking=thinking,
        )
        result = KholasaResult.model_validate(candidate.data)
        citations = [item.citation() for item in evidence]
        evidence_texts = [citation.excerpt for citation in citations]
        scores = [
            ClaimEvidenceValidator.validate(
                claim=f"{card.front} {card.back}",
                source_references=card.source_references,
                evidence_texts=evidence_texts,
            ).score
            for card in result.flashcards
        ]
        scores.append(
            ClaimEvidenceValidator.validate(
                claim=" ".join([result.executive_summary, *result.key_points]),
                source_references=list(range(1, len(evidence_texts) + 1)),
                evidence_texts=evidence_texts,
            ).score
        )
        verified = result.model_copy(update={"citations": citations})
        return self._result(
            result=verified,
            evidence=evidence,
            provider=candidate,
            validation={"status": "valid", "groundedness": sum(scores) / len(scores)},
        )

    def _run_khota(self, *, workspace_id: str, input: dict[str, Any]) -> LabRunResult:
        request = KhotaRequest.model_validate(input)
        evidence = (
            self._evidence(
                workspace_id=workspace_id,
                source_ids=request.source_ids,
                query="",
            )
            if request.source_ids
            else []
        )
        topics = [
            item.chunk.section_title or f"الموضوع {index}" for index, item in enumerate(evidence, 1)
        ]
        # Same deterministic scheduler the production Khota pipeline uses
        # (app/services/khota_scheduler.py) -- Lab and service never diverge
        # on how a plan is built (spec section 17).
        days = build_plan_days(request, topics=topics)
        if not days:
            raise ValidationFailure(
                "No study days remain after exclusions", code="lab_no_study_days"
            )
        result = KhotaResult(
            title="خطة دراسة شخصية",
            strategy_summary=(
                "خطة موزعة محلياً وفق الأيام المتاحة والوقت اليومي والاختبارات القريبة."
            ),
            plan_days=days,
            assumptions=["الجدولة محلية وحتمية؛ لا تنشئ بيانات أداء غير مدخلة."],
            adaptation_rules=["أعد توزيع الجلسات غير المكتملة على أول يوم متاح لاحق."],
            citations=[item.citation() for item in evidence],
        )
        return LabRunResult(
            result=result.model_dump(mode="json"),
            evidence=self._evidence_dump(evidence),
            validation={"status": "valid", "constraint_validation": "PASS"},
            provider={"name": "local_scheduler", "model": None, "latency_ms": 0, "cost_usd": 0.0},
        )

    async def _run_rasheed(
        self, *, workspace_id: str, input: dict[str, Any], thinking: bool | None
    ) -> LabRunResult:
        del workspace_id
        request = RasheedRequest.model_validate(input)
        authoritative = [metric for metric in request.metrics if metric.authoritative]
        if not authoritative:
            raise ValidationFailure(
                "Rasheed needs Lab session or manual metrics", code="lab_metrics_required"
            )
        prompt = get_prompt_registry().get("rasheed_recommend")
        candidate = await self._generate(
            task_type=TaskType.RASHEED_RECOMMENDATIONS,
            user_input=prompt.render_user(
                task_parameters=json.dumps(
                    {"learner_goal": request.learner_goal, "language": request.language},
                    ensure_ascii=False,
                ),
                authority_data=json.dumps(
                    {
                        "metrics": [item.model_dump(mode="json") for item in authoritative],
                        "topic_performance": [
                            item.model_dump(mode="json") for item in request.topic_performance
                        ],
                        "recent_actions": request.recent_actions,
                        "policy": "LAB_SESSION_DATA_ONLY",
                    },
                    ensure_ascii=False,
                ),
            ),
            output_model=RasheedResult,
            thinking=thinking,
        )
        result = RasheedResult.model_validate(candidate.data)
        return self._result(
            result=result,
            evidence=[],
            provider=candidate,
            validation={"status": "valid", "data_policy": "LAB_SESSION_DATA_ONLY"},
        )

    async def _run_sada(
        self, *, workspace_id: str, input: dict[str, Any], thinking: bool | None
    ) -> LabRunResult:
        request = SadaRequest.model_validate(input)
        source = self.storage.get_source(source_id=request.source_id, workspace_id=workspace_id)
        if source.status != "audio_ready":
            raise ValidationFailure(
                "Sada requires an uploaded audio source", code="lab_audio_required"
            )
        raw = await self.stt.transcribe(
            filename=source.filename,
            content=self.storage.read_source_bytes(
                workspace_id=workspace_id, source_id=source.source_id
            ),
            language=request.language,
        )
        raw_segments = [TranscriptSegment.model_validate(item) for item in raw.segments]
        prompt = get_prompt_registry().get("sada_cleanup_transcript")
        segment_timeline = [
            {"start": item.start_seconds, "end": item.end_seconds, "speaker": item.speaker}
            for item in raw_segments
        ]
        candidate = await self._generate(
            task_type=TaskType.SADA_TRANSCRIBE_AUDIO,
            user_input=prompt.render_user(
                task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                raw_transcript=raw.text,
                segment_timeline=json.dumps(segment_timeline, ensure_ascii=False),
            ),
            output_model=SadaCleanupResult,
            thinking=thinking,
        )
        cleanup = SadaCleanupResult.model_validate(candidate.data)
        result = SadaResult(
            full_transcript=raw.text,
            cleaned_transcript=cleanup.cleaned_transcript,
            segments=raw_segments,
            detected_topics=cleanup.detected_topics,
            important_terms=cleanup.important_terms,
            duration_seconds=raw.duration_seconds,
            language=request.language,
            warnings=cleanup.warnings,
        )
        return self._result(
            result=result,
            evidence=[],
            provider=candidate,
            validation={
                "status": "valid",
                "stt_provider": "local_whisper",
                "raw_transcript_preserved": True,
                "transcript_source_can_be_saved": True,
            },
        )

    def _result(
        self,
        *,
        result: StrictModel,
        evidence: list[LocalEvidence],
        provider: ProviderResult,
        validation: dict[str, Any],
    ) -> LabRunResult:
        return LabRunResult(
            result=result.model_dump(mode="json"),
            evidence=self._evidence_dump(evidence),
            validation=validation,
            provider={
                "name": provider.account.value,
                "model": provider.model,
                "response_id": provider.response_id,
                "latency_ms": provider.latency_ms,
                "input_tokens": provider.usage.input_tokens,
                "output_tokens": provider.usage.output_tokens,
                "cost_usd": provider.estimated_cost_usd,
                "prompt_trace": provider.metadata.get("prompt_trace", {}),
            },
        )
