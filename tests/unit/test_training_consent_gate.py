from __future__ import annotations

from typing import Any

from app.models.ai_job import AIJob, AIOutput
from app.models.enums import Character, JobStatus, ProviderAccount, TaskType
from app.models.feedback import AIFeedback
from app.training.candidate_builder import build_candidate


def _job() -> AIJob:
    return AIJob(
        user_id="user-1",
        request_id="00000000-0000-0000-0000-000000000001",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        character=Character.FAHES,
        status=JobStatus.COMPLETED,
        idempotency_hash="a" * 64,
        input_hash="b" * 64,
        request_payload={"topic": "algebra"},
    )


def _output(job: AIJob) -> AIOutput:
    return AIOutput(
        job_id=job.id,
        result_json={"questions": ["What is x?"]},
        provider_account=ProviderAccount.PRIMARY,
        model_name="gpt-test",
    )


def _feedback(
    output: AIOutput,
    *,
    consent: bool,
    is_helpful: bool = True,
    corrected: dict[str, Any] | None = None,
) -> AIFeedback:
    return AIFeedback(
        output_id=output.id,
        user_id="user-1",
        rating=5 if is_helpful else 1,
        is_helpful=is_helpful,
        consent_for_training=consent,
        corrected_output=corrected,
    )


def test_no_consent_never_produces_a_training_candidate() -> None:
    """The single most important guarantee in this module: a user who did not
    opt in to training data collection can never have their feedback turn
    into a TrainingDatasetCandidate row -- there is nothing downstream (the
    exporter's own APPROVED+anonymized filter) that could let this back in,
    because the row is simply never created in the first place."""
    job = _job()
    output = _output(job)
    feedback = _feedback(output, consent=False, is_helpful=True)

    assert build_candidate(job=job, output=output, feedback=feedback) is None


def test_no_consent_is_enforced_even_for_a_corrected_helpful_answer() -> None:
    """Consent is checked first, unconditionally -- a corrected/helpful
    answer (which would otherwise qualify) still must not leak through."""
    job = _job()
    output = _output(job)
    feedback = _feedback(
        output, consent=False, is_helpful=True, corrected={"questions": ["Fixed?"]}
    )

    assert build_candidate(job=job, output=output, feedback=feedback) is None


def test_unhelpful_feedback_without_a_correction_is_evaluation_only() -> None:
    """Consented but unhelpful-with-no-correction is evidence for quality
    evaluation, not a ground-truth training example -- also excluded, for a
    different reason than missing consent."""
    job = _job()
    output = _output(job)
    feedback = _feedback(output, consent=True, is_helpful=False, corrected=None)

    assert build_candidate(job=job, output=output, feedback=feedback) is None


def test_consenting_helpful_feedback_produces_an_anonymized_candidate() -> None:
    # Purely structural/numeric payload -- no natural-language string
    # anywhere -- so the anonymizer's conservative free-text heuristic (any
    # Latin/Arabic letter is presumed unprovable and flags for review, see
    # app.training.anonymizer._NATURAL_LANGUAGE) has nothing to flag, and
    # this candidate can be proven anonymous automatically.
    job = _job()
    job.request_payload = {"question_count": 5, "difficulty": 3}
    output = _output(job)
    output.result_json = {"score": 0.9, "count": 2}
    feedback = _feedback(output, consent=True, is_helpful=True)

    candidate = build_candidate(job=job, output=output, feedback=feedback)

    assert candidate is not None
    assert candidate.output_id == output.id
    assert candidate.feedback_id == feedback.id
    assert candidate.task_type == TaskType.FAHES_GENERATE_QUIZ.value
    assert candidate.anonymized is True
    assert candidate.review_notes is None


def test_consenting_feedback_with_unverified_free_text_is_flagged_for_human_review() -> None:
    job = _job()
    job.request_payload = {"note": "My name is Ahmad and I live in Damascus"}
    output = _output(job)
    feedback = _feedback(output, consent=True, is_helpful=True)

    candidate = build_candidate(job=job, output=output, feedback=feedback)

    assert candidate is not None
    assert candidate.anonymized is False
    assert candidate.review_notes == "Human privacy review required for unverified free text"
