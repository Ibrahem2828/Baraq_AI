from __future__ import annotations

from app.models.ai_job import AIJob, AIOutput
from app.models.feedback import AIFeedback, TrainingDatasetCandidate
from app.training.anonymizer import anonymize_value


def build_candidate(
    *, job: AIJob, output: AIOutput, feedback: AIFeedback
) -> TrainingDatasetCandidate | None:
    if not feedback.consent_for_training:
        return None
    expected = feedback.corrected_output or output.result_json
    # Negative feedback without a correction is evidence for evaluation, not ground truth.
    if not feedback.is_helpful and feedback.corrected_output is None:
        return None
    input_data, input_report = anonymize_value(job.request_payload)
    expected_data, expected_report = anonymize_value(expected)
    report = {
        key: input_report.get(key, 0) + expected_report.get(key, 0)
        for key in set(input_report) | set(expected_report)
    }
    return TrainingDatasetCandidate(
        output_id=output.id,
        feedback_id=feedback.id,
        task_type=job.task_type.value,
        input_json=input_data,
        expected_output_json=expected_data,
        anonymized=True,
        pii_report=report,
    )
