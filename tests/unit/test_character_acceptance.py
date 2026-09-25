from __future__ import annotations

import uuid
from typing import Any, ClassVar, cast

import pytest

import app.pipelines.fahes as fahes_module
import app.pipelines.kholasa as kholasa_module
from app.models.ai_job import AIJob
from app.models.enums import Character, JobStatus, ProviderAccount, TaskType
from app.pipelines.base import PipelineContext
from app.pipelines.fahes import FahesPipeline
from app.pipelines.kholasa import KholasaPipeline
from app.pipelines.khota import KhotaPipeline
from app.pipelines.rasheed import RasheedPipeline
from app.providers.base import ProviderResult, ProviderUsage
from app.rag.repository import RetrievedChunk
from app.rag.retriever import RAGContext, RAGRetriever
from app.schemas.backend import LearnerContext
from app.schemas.common import Citation
from app.schemas.fahes import FahesResult
from app.schemas.kholasa import KholasaResult
from app.schemas.khota import KhotaNarrative, KhotaResult
from app.schemas.rasheed import RasheedResult

SOURCE_ID = "source-ar-1"
SOURCE_SHA = "a" * 64
ARABIC_FACT = "تتكون آلية التحقق في برّاق من سبع مراحل مستقلة ومتتابعة."


class _Session:
    pass


class _Ingestion:
    def __init__(self) -> None:
        self.embeddings = object()
        self.calls: list[dict[str, str | None]] = []

    async def ensure_ingested(self, **kwargs: str | None) -> object:
        self.calls.append(kwargs)
        return object()


class _Backend:
    async def get_learner_context(self, *, user_id: str) -> LearnerContext:
        return LearnerContext(user_id=user_id, daily_study_minutes=90)


class _Generation:
    def __init__(self, outputs: dict[type, dict[str, Any]]) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> ProviderResult:
        self.calls.append(kwargs)
        output_model = cast(type, kwargs["output_model"])
        return ProviderResult(
            data=self.outputs[output_model],
            account=ProviderAccount.MOCK,
            model="deterministic-acceptance",
            response_id="acceptance-1",
            usage=ProviderUsage(input_tokens=10, output_tokens=10, total_tokens=20),
        )


class _Retriever:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def retrieve(self, **kwargs: Any) -> RAGContext:
        self.calls.append({"method": "retrieve", **kwargs})
        return self._context(kwargs)

    async def retrieve_across(self, **kwargs: Any) -> RAGContext:
        self.calls.append({"method": "retrieve_across", **kwargs})
        return self._context(kwargs)

    def _context(self, kwargs: dict[str, Any]) -> RAGContext:
        assert kwargs["user_id"] == "user-a"
        assert kwargs["project_id"] == "project-a"
        assert kwargs["source_ids"] == [SOURCE_ID]
        assert kwargs["source_versions"] == {SOURCE_ID: SOURCE_SHA}
        citation = Citation(
            evidence_id=uuid.uuid4(),
            source_id=SOURCE_ID,
            content_sha256=SOURCE_SHA,
            chunk_id=uuid.uuid4(),
            section_title="آلية التحقق",
            excerpt=(
                f"{ARABIC_FACT} كم عدد مراحل آلية التحقق في برّاق؟ سبع مراحل. "
                "يوضح المصدر صراحة أن آلية التحقق في برّاق تتكون من سبع مراحل مستقلة."
            ),
            relevance_score=0.99,
        )
        return RAGContext(
            text=f"[S1] source_id={SOURCE_ID}\n{citation.excerpt}",
            citations=[citation],
            chunks=[],
            suspicious_source_detected=False,
        )


def _job(task_type: TaskType, character: Character, payload: dict[str, Any]) -> AIJob:
    return AIJob(
        id=uuid.uuid4(),
        user_id="user-a",
        project_id="project-a",
        request_id=str(uuid.uuid4()),
        task_type=task_type,
        character=character,
        status=JobStatus.RETRIEVING,
        request_payload=payload,
        source_ids=list(payload.get("source_ids") or []),
        source_versions={source_id: SOURCE_SHA for source_id in payload.get("source_ids") or []},
        allow_fallback=True,
    )


def _context(job: AIJob, generation: _Generation, ingestion: _Ingestion) -> PipelineContext:
    return PipelineContext(
        session=cast(Any, _Session()),
        job=job,
        backend=cast(Any, _Backend()),
        generation=cast(Any, generation),
        ingestion=cast(Any, ingestion),
    )


@pytest.mark.asyncio
async def test_fahes_is_grounded_in_the_selected_arabic_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Retriever.calls.clear()
    monkeypatch.setattr(fahes_module, "RAGRetriever", _Retriever)
    output = {
        "title": "اختبار آلية التحقق (مقتطفات من المصدر رقم 9)",
        "description": "اختبار مبني حصريًا على المصدر المحدد.",
        "questions": [{
            "question_type": "mcq",
            # A leaked context label: the learner must never see it.
            "question": "بحسب النص في S1، كم عدد مراحل آلية التحقق في برّاق؟",
            "choices": ["سبع مراحل", "خمس مراحل"],
            "correct_answer_index": 0,
            "explanation": "يوضح المصدر أن آلية التحقق في برّاق تتكون من سبع مراحل مستقلة.",
            "difficulty": "medium",
            "topic": "آلية التحقق",
            "source_references": [1],
        }],
        "covered_topics": ["آلية التحقق"],
        "warnings": [],
        "citations": [],
    }
    generation = _Generation({FahesResult: output})
    ingestion = _Ingestion()
    job = _job(
        TaskType.FAHES_GENERATE_QUIZ,
        Character.FAHES,
        {
            "source_ids": [SOURCE_ID],
            "question_count": 3,
            "question_types": ["mcq"],
            "language": "ar",
        },
    )

    result = await FahesPipeline().execute(_context(job, generation, ingestion))
    validated = FahesResult.model_validate(result.result_json)

    assert validated.questions[0].correct_answer_index == 0
    assert "سبع مراحل" in validated.questions[0].choices
    assert validated.citations[0].source_id == SOURCE_ID
    assert result.groundedness_score is not None and result.groundedness_score > 0
    assert ingestion.calls == [{
        "source_id": SOURCE_ID,
        "user_id": "user-a",
        "project_id": "project-a",
        "expected_content_sha256": SOURCE_SHA,
    }]
    # No topic was given, so the whole selected source is in scope: it is
    # sampled evenly rather than ranked against a stand-in query (production
    # 2026-09-25: questions about a textbook's cover and table of contents).
    assert _Retriever.calls[0]["method"] == "retrieve_across"
    assert (
        validated.questions[0].question
        == "بحسب النص في المصدر، كم عدد مراحل آلية التحقق في برّاق؟"
    )
    assert validated.title == "اختبار آلية التحقق"
    # References stay intact for the backend; only learner text is cleaned.
    assert validated.questions[0].source_references == [1]


@pytest.mark.asyncio
async def test_kholasa_reuses_the_same_source_version_and_preserves_the_unique_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Retriever.calls.clear()
    monkeypatch.setattr(kholasa_module, "RAGRetriever", _Retriever)
    output = {
        "title": "خلاصة آلية التحقق",
        "executive_summary": ARABIC_FACT + " ويعرض المصدر وظيفة كل مرحلة بوضوح.",
        "detailed_summary": (
            ARABIC_FACT
            + " وتعمل هذه المراحل بصورة متتابعة لضمان سلامة النتائج التعليمية."
        ),
        "key_points": [ARABIC_FACT],
        "important_terms": ["آلية التحقق"],
        "covered_topics": ["آلية التحقق"],
        "review_questions": ["كم عدد مراحل آلية التحقق؟"],
        "flashcards": [],
        "limitations": [],
        "citations": [],
    }
    generation = _Generation({KholasaResult: output})
    ingestion = _Ingestion()
    job = _job(
        TaskType.KHOLASA_GENERATE_SUMMARY,
        Character.KHOLASA,
        {
            "source_ids": [SOURCE_ID],
            "summary_length": "medium",
            "focus_topics": ["آلية التحقق"],
            "include_review_questions": True,
            "include_flashcards": False,
            "language": "ar",
        },
    )

    result = await KholasaPipeline().execute(_context(job, generation, ingestion))
    validated = KholasaResult.model_validate(result.result_json)

    assert ARABIC_FACT in validated.executive_summary
    assert validated.citations[0].source_id == SOURCE_ID
    # Focus topics were given: ranked retrieval with the configured floor.
    assert _Retriever.calls[0]["method"] == "retrieve"
    assert "min_similarity" not in _Retriever.calls[0]
    assert result.groundedness_score is not None and result.groundedness_score > 0
    assert ingestion.calls[0]["expected_content_sha256"] == SOURCE_SHA


@pytest.mark.asyncio
async def test_khota_builds_a_valid_deterministic_schedule_within_bounds() -> None:
    output = {
        "title": "خطة مراجعة منظمة",
        "strategy_summary": "توزيع وقت الدراسة على جلسات قصيرة ومتوازنة ضمن الأيام المتاحة.",
        "assumptions": [],
        "adaptation_rules": ["انقل المهمة غير المكتملة إلى اليوم التالي المتاح."],
    }
    generation = _Generation({KhotaNarrative: output})
    ingestion = _Ingestion()
    job = _job(
        TaskType.KHOTA_GENERATE_PLAN,
        Character.KHOTA,
        {
            "source_ids": [],
            "subject_ids": ["subject-1"],
            "subject_names": {"subject-1": "الفيزياء"},
            "start_date": "2026-09-20",
            "end_date": "2026-09-22",
            "daily_available_minutes": 90,
            "exam_dates": {},
            "weak_topics": ["القوة"],
            "excluded_dates": [],
            "preferred_session_minutes": 30,
            "language": "ar",
        },
    )

    result = await KhotaPipeline().execute(_context(job, generation, ingestion))
    validated = KhotaResult.model_validate(result.result_json)

    assert validated.plan_days
    assert all(day.total_minutes <= 90 for day in validated.plan_days)
    assert all(
        "2026-09-20" <= str(day.date) <= "2026-09-22" for day in validated.plan_days
    )
    assert ingestion.calls == []
    # Tasks carry the subject's name, not its id.
    assert {task.subject_name for day in validated.plan_days for task in day.tasks} == {
        "الفيزياء"
    }


@pytest.mark.asyncio
async def test_rasheed_recommendations_reference_authoritative_topics_only() -> None:
    output = {
        "performance_summary": "الأداء قوي في الحركة ويحتاج إلى تحسين منظم في موضوع القوة.",
        "strengths": ["الحركة"],
        "weaknesses": ["القوة"],
        "recommendations": [{
            "title": "تدريب القوة",
            "action": "حل مسائل إضافية عن القوة ومراجعة الأخطاء بعد كل محاولة.",
            "reason": "درجة القوة أقل بوضوح من درجة الحركة في السجل الفعلي.",
            "priority": "now",
            "success_measure": "الوصول إلى 75 بالمئة في الاختبار التالي.",
            "related_topics": ["القوة"],
        }],
        "next_best_action": "ابدأ الآن بمجموعة مسائل القوة.",
        "confidence_note": "التحليل مبني على عشرين إجابة موثقة.",
    }
    generation = _Generation({RasheedResult: output})
    ingestion = _Ingestion()
    job = _job(
        TaskType.RASHEED_RECOMMENDATIONS,
        Character.RASHEED,
        {
            "metrics": [{
                "name": "average_quiz_percentage",
                "value": 70,
                "unit": "percent",
                "period": "all_time",
                "authoritative": True,
            }],
            "topic_performance": [
                {"topic": "الحركة", "score": 90, "answered_questions": 10},
                {"topic": "القوة", "score": 50, "answered_questions": 10},
            ],
            "recent_actions": [],
            "language": "ar",
        },
    )

    result = await RasheedPipeline().execute(_context(job, generation, ingestion))
    validated = RasheedResult.model_validate(result.result_json)

    assert validated.strengths == ["الحركة"]
    assert validated.weaknesses == ["القوة"]
    assert validated.recommendations[0].related_topics == ["القوة"]
    assert result.groundedness_score is not None and result.groundedness_score > 0
    assert ingestion.calls == []


class _ArabicEmbeddings:
    async def embed_query(self, query: str, routing_key: str) -> list[float]:
        assert "سبع مراحل" in query
        assert routing_key == "arabic-gate"
        return [0.1] * 8


class _ArabicRepository:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def retrieve(self, **kwargs: Any) -> list[RetrievedChunk]:
        self.kwargs = kwargs
        return [
            RetrievedChunk(
                chunk_id=uuid.uuid4(),
                source_id=SOURCE_ID,
                content_sha256=SOURCE_SHA,
                title="بروتوكول برّاق",
                page_number=1,
                section_title="سبع مراحل التحقق",
                text=ARABIC_FACT,
                score=0.9,
                semantic_score=0.9,
            )
        ]


@pytest.mark.asyncio
async def test_arabic_rag_retrieves_the_unique_fact_with_exact_scope() -> None:
    retriever = RAGRetriever(
        session=cast(Any, _Session()), embeddings=cast(Any, _ArabicEmbeddings())
    )
    repository = _ArabicRepository()
    retriever.repository = cast(Any, repository)

    result = await retriever.retrieve(
        user_id="user-a",
        project_id="project-a",
        source_ids=[SOURCE_ID],
        source_versions={SOURCE_ID: SOURCE_SHA},
        query="كم عدد مراحل آلية التحقق؟ سبع مراحل",
        routing_key="arabic-gate",
    )

    assert ARABIC_FACT in result.text
    assert result.citations[0].source_id == SOURCE_ID
    assert result.citations[0].content_sha256 == SOURCE_SHA
    assert repository.kwargs["user_id"] == "user-a"
    assert repository.kwargs["project_id"] == "project-a"
    assert repository.kwargs["source_ids"] == [SOURCE_ID]
    assert repository.kwargs["source_versions"] == {SOURCE_ID: SOURCE_SHA}


@pytest.mark.asyncio
async def test_an_unsupported_question_is_dropped_not_the_whole_quiz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fahes_module, "RAGRetriever", _Retriever)
    supported = {
        "question_type": "mcq",
        "question": "كم عدد مراحل آلية التحقق في برّاق؟",
        "choices": ["سبع مراحل", "خمس مراحل"],
        "correct_answer_index": 0,
        "explanation": "يوضح المصدر أن آلية التحقق في برّاق تتكون من سبع مراحل مستقلة.",
        "difficulty": "medium",
        "topic": "آلية التحقق",
        "source_references": [1],
    }
    unsupported = {
        **supported,
        "question": "ما عاصمة فرنسا الواقعة على نهر السين؟",
        "choices": ["باريس", "ليون"],
        "explanation": "باريس هي العاصمة الفرنسية المعروفة بمعالمها التاريخية.",
    }
    output = {
        "title": "اختبار آلية التحقق",
        "description": "اختبار مبني على المصدر.",
        "questions": [supported, unsupported],
        "covered_topics": ["آلية التحقق"],
        "warnings": [],
        "citations": [],
    }
    job = _job(
        TaskType.FAHES_GENERATE_QUIZ,
        Character.FAHES,
        {
            "source_ids": [SOURCE_ID],
            "question_count": 3,
            "question_types": ["mcq"],
            "language": "ar",
        },
    )

    result = await FahesPipeline().execute(
        _context(job, _Generation({FahesResult: output}), _Ingestion())
    )

    questions = FahesResult.model_validate(result.result_json).questions
    assert [question.question for question in questions] == [supported["question"]]


@pytest.mark.asyncio
async def test_an_unsupported_flashcard_is_dropped_not_the_whole_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kholasa_module, "RAGRetriever", _Retriever)
    output = {
        "title": "خلاصة آلية التحقق",
        "executive_summary": ARABIC_FACT + " ويعرض المصدر وظيفة كل مرحلة بوضوح.",
        "detailed_summary": ARABIC_FACT + " وتعمل هذه المراحل بصورة متتابعة.",
        "key_points": [ARABIC_FACT],
        "important_terms": ["آلية التحقق"],
        "covered_topics": ["آلية التحقق"],
        "review_questions": [],
        "flashcards": [
            {
                "front": "كم مرحلة في آلية التحقق في برّاق؟",
                "back": "سبع مراحل مستقلة",
                "source_references": [1],
            },
            {"front": "ما عاصمة فرنسا؟", "back": "باريس على نهر السين", "source_references": [1]},
        ],
        "limitations": [],
        "citations": [],
    }
    job = _job(
        TaskType.KHOLASA_GENERATE_SUMMARY,
        Character.KHOLASA,
        {
            "source_ids": [SOURCE_ID],
            "summary_length": "medium",
            "include_review_questions": False,
            "include_flashcards": True,
            "language": "ar",
        },
    )

    result = await KholasaPipeline().execute(
        _context(job, _Generation({KholasaResult: output}), _Ingestion())
    )

    cards = KholasaResult.model_validate(result.result_json).flashcards
    assert [card.front for card in cards] == ["كم مرحلة في آلية التحقق في برّاق؟"]


@pytest.mark.asyncio
async def test_rasheed_without_topic_data_reports_no_topic_weaknesses() -> None:
    """Khota plans sessions on Rasheed's weaknesses: a sentence about missing
    data there became a study task (production 2026-09-25)."""
    output = {
        "performance_summary": "لا توجد بيانات كافية بعد لتحليل الأداء حسب الموضوع.",
        "strengths": [],
        "weaknesses": ["لم تُسجّل أي محاولات في الاختبارات القصيرة (quiz_attempts = 0)."],
        "recommendations": [{
            "title": "ابدأ بأول اختبار",
            "action": "أنشئ اختبارًا قصيرًا من فاحص على أحد مصادرك ثم راجع أخطاءك.",
            "reason": "لا توجد بعد محاولات اختبار يمكن تحليل الأداء على أساسها.",
            "priority": "now",
            "success_measure": "إكمال أول اختبار قصير هذا الأسبوع.",
            "related_topics": [],
        }],
        "next_best_action": "ابدأ باختبار قصير من فاحص على أحد مصادرك.",
        "confidence_note": "التحليل مبني على بيانات محدودة جدًا.",
    }
    job = _job(
        TaskType.RASHEED_RECOMMENDATIONS,
        Character.RASHEED,
        {
            "metrics": [{
                "name": "quiz_attempts",
                "value": 0,
                "unit": "count",
                "period": "all_time",
                "authoritative": True,
            }],
            "topic_performance": [],
            "recent_actions": [],
            "language": "ar",
        },
    )

    result = await RasheedPipeline().execute(
        _context(job, _Generation({RasheedResult: output}), _Ingestion())
    )

    assert RasheedResult.model_validate(result.result_json).weaknesses == []
