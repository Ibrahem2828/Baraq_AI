"""Deterministic mock provider for unit tests and PR-gate CI.

The mock never touches a network. It returns a fixed, schema-valid payload per
task type so pipelines, validators and the job state machine can be exercised
without a live Gemini/OpenAI credential (spec section 6). ``generate_structured``
still runs the *real* ``output_model.model_validate`` path -- a broken pipeline
schema fails here exactly as it would against a live provider.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError, ValidationFailure
from app.models.enums import Provider, ProviderAccount
from app.providers.base import (
    EmbeddingResult,
    LLMProvider,
    ProviderResult,
    ProviderUsage,
    TranscriptionResult,
)

# One deterministic, schema-valid success payload per task type. Kept in code
# (not a file) because these are unit-test fixtures for code/contract safety,
# not integration replay data -- spec section 6 draws that line explicitly.
MOCK_SUCCESS_PAYLOADS: dict[str, dict[str, Any]] = {
    "fahes_generate_quiz": {
        "title": "اختبار تجريبي محاكى",
        "description": "أسئلة تجريبية من مزود Mock لأغراض الاختبار فقط.",
        "questions": [
            {
                "question_type": "mcq",
                "question": "ما العاصمة المذكورة في المصدر المرفق كمثال؟",
                "choices": ["الرياض", "القاهرة", "عمّان", "بغداد"],
                "correct_answer_index": 0,
                "explanation": "المصدر يذكر الرياض كمثال توضيحي رقم واحد.",
                "difficulty": "easy",
                "topic": "جغرافيا تجريبية",
                "source_references": [1],
            }
        ],
        "covered_topics": ["جغرافيا تجريبية"],
        "warnings": [],
        "citations": [],
    },
    "kholasa_generate_summary": {
        "title": "ملخص تجريبي محاكى",
        "executive_summary": "هذا ملخص تنفيذي مصطنع يفي بالحد الأدنى للطول المطلوب من مزود Mock.",
        "detailed_summary": (
            "هذا ملخص تفصيلي مصطنع من مزود Mock يفي بالحد الأدنى للطول المطلوب "
            "لاختبار مسار التحقق والاستشهاد دون استدعاء أي مزود حقيقي."
        ),
        "key_points": ["نقطة تجريبية أولى", "نقطة تجريبية ثانية"],
        "important_terms": ["مصطلح تجريبي"],
        "covered_topics": ["موضوع تجريبي"],
        "review_questions": ["سؤال مراجعة تجريبي؟"],
        "flashcards": [
            {
                "front": "سؤال بطاقة تجريبية",
                "back": "إجابة بطاقة تجريبية",
                "source_references": [1],
            }
        ],
        "limitations": [],
        "citations": [],
    },
    "rasheed_recommendations": {
        "performance_summary": (
            "أداء تجريبي مصطنع من مزود Mock يستند إلى المقاييس المرسلة فقط لاختبار المسار."
        ),
        "strengths": ["نقطة قوة تجريبية"],
        "weaknesses": ["نقطة ضعف تجريبية"],
        "recommendations": [
            {
                "title": "توصية تجريبية",
                "action": "راجع الموضوع الأضعف المذكور في المقاييس المرسلة.",
                "reason": "المقياس المرسل يظهر أداءً أقل من المتوسط في هذا الموضوع.",
                "priority": "this_week",
                "success_measure": "ارتفاع الدرجة في المحاولة التالية.",
                "related_topics": ["موضوع تجريبي"],
            }
        ],
        "next_best_action": "أعد اختبار الموضوع الأضعف خلال هذا الأسبوع.",
        "confidence_note": "استنتاج وصفي غير معايَر إحصائياً؛ لأغراض اختبار Mock فقط.",
    },
    "sada_transcribe_audio": {
        "full_transcript": "هذا نص تفريغ خام تجريبي من مزود Mock.",
        "cleaned_transcript": "هذا نص منظف تجريبي من مزود Mock.",
        "segments": [],
        "detected_topics": ["موضوع تجريبي"],
        "important_terms": ["مصطلح تجريبي"],
        "duration_seconds": 5.0,
        "language": "ar",
        "warnings": [],
    },
    "khota_generate_plan": {
        "title": "خطة دراسة تجريبية محاكاة",
        "strategy_summary": (
            "استراتيجية سردية تجريبية من مزود Mock تشرح توزيع الخطة الحتمية المرفقة."
        ),
        "assumptions": [],
        "adaptation_rules": ["أعد توزيع الجلسات غير المكتملة على أول يوم متاح لاحق."],
    },
}


class MockProvider(LLMProvider):
    account = ProviderAccount.MOCK
    provider_family = Provider.MOCK

    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        output_model: type[BaseModel],
        schema_name: str,
        max_output_tokens: int,
        reasoning_effort: str | None,
        metadata: dict[str, str],
    ) -> ProviderResult:
        del instructions, user_input, max_output_tokens, reasoning_effort
        started = time.perf_counter()
        task_type = metadata.get("task_type", schema_name)
        payload = MOCK_SUCCESS_PAYLOADS.get(task_type)
        if payload is None:
            raise ProviderError(
                f"No mock fixture registered for task_type '{task_type}'",
                code="mock_fixture_missing",
                retryable=False,
            )
        try:
            validated = output_model.model_validate(payload)
        except ValidationError as exc:
            raise ValidationFailure(
                "Mock fixture does not match the current domain schema",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        return ProviderResult(
            data=validated.model_dump(mode="json"),
            account=self.account,
            model=model,
            response_id=f"mock-{task_type}",
            usage=ProviderUsage(input_tokens=0, output_tokens=0, total_tokens=0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost_usd=0.0,
            metadata={"simulated": True, "provider_mode": "mock"},
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[[0.0] * 8 for _ in texts],
            account=self.account,
            model=model,
            usage=ProviderUsage(),
            latency_ms=0,
            estimated_cost_usd=0.0,
        )

    async def transcribe(
        self,
        *,
        model: str,
        filename: str,
        content: bytes,
        language: str | None,
        prompt: str | None,
        diarize: bool,
    ) -> TranscriptionResult:
        del filename, content, prompt, diarize
        return TranscriptionResult(
            text="هذا نص تفريغ محاكى من مزود Mock.",
            segments=[{"start_seconds": 0.0, "end_seconds": 2.0, "text": "نص محاكى"}],
            account=self.account,
            model=model,
            response_id="mock-transcription",
            usage=ProviderUsage(),
            latency_ms=1,
            estimated_cost_usd=0.0,
        )

    async def moderate(self, *, model: str, inputs: list[str]) -> list[dict[str, Any]]:
        del model
        return [{"flagged": False, "categories": {}} for _ in inputs]
