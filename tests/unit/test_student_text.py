"""Retrieval labels never reach what a learner reads."""

from __future__ import annotations

import pytest

from app.pipelines.student_text import clean_student_text, strip_source_labels
from app.schemas.khota import KhotaNarrative, KhotaRequest
from app.services.khota_scheduler import build_plan_days


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("بحسب النص في S4، كيف يمرّ السائل؟", "بحسب النص في المصدر، كيف يمرّ السائل؟"),
        ("أيٌّ من التالي ورد في القائمة (S1)؟", "أيٌّ من التالي ورد في القائمة؟"),
        ("كما ورد [S2، S3] في الدرس.", "كما ورد في الدرس."),
        ("ملخّص الوحدة (مقتطفات من المصدر رقم 9)", "ملخّص الوحدة"),
        ("كما في المصدر رقم 9 أعلاه", "كما في المصدر أعلاه"),
        ("As stated in S2, cells divide.", "As stated in the source, cells divide."),
        # Not labels: chemistry, words, and ordinary text stay as they are.
        ("SO2 و SS1 و S12x", "SO2 و SS1 و S12x"),
        ("الخلية وحدة بناء الكائن الحي.", "الخلية وحدة بناء الكائن الحي."),
    ],
)
def test_labels_are_removed_from_learner_text(text: str, expected: str) -> None:
    assert strip_source_labels(text) == expected


def test_every_learner_field_of_a_result_is_cleaned() -> None:
    narrative = KhotaNarrative(
        title="خطة الفيزياء (S1)",
        strategy_summary="نبدأ بما ورد في S2 ثم ننتقل إلى التطبيق العملي على المسائل.",
        assumptions=["الوقت المتاح ثابت [S3]."],
        adaptation_rules=[],
    )

    cleaned = clean_student_text(narrative)

    assert cleaned.title == "خطة الفيزياء"
    assert "S2" not in cleaned.strategy_summary
    assert cleaned.assumptions == ["الوقت المتاح ثابت."]


def test_a_plan_without_topics_reviews_each_subject_by_name() -> None:
    request = KhotaRequest.model_validate(
        {
            "subject_ids": ["7", "8"],
            "subject_names": {"7": "الكيمياء", "8": "التاريخ"},
            "start_date": "2026-09-20",
            "end_date": "2026-09-20",
            "daily_available_minutes": 60,
            "preferred_session_minutes": 30,
        }
    )

    tasks = build_plan_days(request, topics=[])[0].tasks

    assert [(task.subject_name, task.topic) for task in tasks] == [
        ("الكيمياء", "مراجعة الكيمياء"),
        ("التاريخ", "مراجعة التاريخ"),
    ]


def test_an_older_backend_without_subject_names_still_plans() -> None:
    request = KhotaRequest.model_validate(
        {
            "subject_ids": ["7"],
            "start_date": "2026-09-20",
            "end_date": "2026-09-20",
            "daily_available_minutes": 30,
            "preferred_session_minutes": 30,
        }
    )

    task = build_plan_days(request, topics=[])[0].tasks[0]

    assert task.subject_name == "7"
