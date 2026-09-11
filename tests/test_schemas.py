from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.fahes import FahesQuestion, QuestionDifficulty, QuestionKind
from app.schemas.khota import KhotaRequest, PlanDay, PlannedTask, StudyTaskType, TaskPriority


def test_fahes_rejects_duplicate_choices() -> None:
    with pytest.raises(ValidationError):
        FahesQuestion(
            question_type=QuestionKind.MCQ,
            question="ما التعريف الصحيح للمفهوم المذكور في المصدر؟",
            choices=["الإجابة", "الإجابة"],
            correct_answer_index=0,
            explanation="يوضح المصدر أن الخيار الأول هو التعريف الصحيح.",
            difficulty=QuestionDifficulty.MEDIUM,
            topic="تعريف",
            source_references=[1],
        )


def test_true_false_requires_two_choices() -> None:
    with pytest.raises(ValidationError):
        FahesQuestion(
            question_type=QuestionKind.TRUE_FALSE,
            question="هل العبارة الآتية صحيحة وفق المصدر؟",
            choices=["صحيح", "خطأ", "غير محدد"],
            correct_answer_index=0,
            explanation="العبارة مذكورة صراحة في المصدر.",
            difficulty=QuestionDifficulty.EASY,
            topic="مفهوم",
            source_references=[1],
        )


def test_khota_rejects_reversed_dates() -> None:
    with pytest.raises(ValidationError):
        KhotaRequest(
            subject_ids=["1"],
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 1),
            daily_available_minutes=120,
        )


def test_plan_day_requires_exact_total() -> None:
    with pytest.raises(ValidationError):
        PlanDay(
            date=date(2026, 8, 1),
            total_minutes=30,
            tasks=[
                PlannedTask(
                    subject_id="1",
                    subject_name="الرياضيات",
                    topic="التفاضل",
                    task_type=StudyTaskType.PRACTICE,
                    estimated_minutes=45,
                    priority=TaskPriority.HIGH,
                    reason="لأن الموضوع قريب من موعد الاختبار ويحتاج إلى تدريب.",
                )
            ],
        )
