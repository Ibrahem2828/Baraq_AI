from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

import pytest

from app.models.ai_job import AIJob, AIOutput, JobDispatchOutboxEvent
from app.models.enums import JobStatus, ProviderAccount
from app.rag.grounding import ClaimEvidenceValidator, transcript_preservation_score
from app.services.webhook_delivery import (
    build_result_webhook_payload,
    build_terminal_webhook_payload,
)

# Provider-candidate/router-selection coverage now lives in
# tests/unit/test_provider_router.py against the real ProviderCandidate shape.


def test_claim_evidence_validator_rejects_unsupported_claim() -> None:
    with pytest.raises(Exception, match="not supported"):
        ClaimEvidenceValidator.validate(
            claim="الجاذبية تساوي سرعة الضوء",
            source_references=[1],
            evidence_texts=["يتحدث النص عن دورة الماء والتبخر."],
        )


def test_claim_evidence_validator_calculates_non_cosmetic_score() -> None:
    result = ClaimEvidenceValidator.validate(
        claim="قانون نيوتن الثاني يربط القوة بالتسارع",
        source_references=[1],
        evidence_texts=["ينص قانون نيوتن الثاني على أن القوة تتناسب مع التسارع."],
    )
    assert 0 < result.score <= 1


def test_claim_evidence_validator_ignores_arabic_punctuation() -> None:
    result = ClaimEvidenceValidator.validate(
        claim="النقطة الثانية مهمة",
        source_references=[1],
        evidence_texts=["النقطة الثانية، مهمة؟"],
    )

    assert result.score == 1.0


def test_sada_preservation_is_measured_from_transcript_tokens() -> None:
    score = transcript_preservation_score(
        raw_transcript="قانون نيوتن الثاني يشرح القوة والتسارع",
        cleaned_transcript="قانون نيوتن الثاني يشرح القوة والتسارع بوضوح",
    )
    assert score == 1.0


def test_result_webhook_has_stable_event_and_trace_identifiers() -> None:
    event = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000020"))
    job = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000021"),
        backend_request_id="django-job-1",
        request_id="00000000-0000-0000-0000-000000000022",
        prompt_name="fahes_generate_quiz",
        prompt_version="1.1.0",
        prompt_checksum="cc05892c406308a3018dfbb6ffd781722d94ee93bb5779639c6b79ee0223c669",
    )
    output = SimpleNamespace(
        result_json={"title": "verified"},
        validation_report={"status": "valid"},
        quality_score=0.8,
        groundedness_score=0.75,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        estimated_cost_usd=0.001,
        provider_account=ProviderAccount.PRIMARY,
        model_name="deterministic-test-model",
        provider_response_id="response-1",
        warnings=[],
        security_flags=[],
    )

    payload = build_result_webhook_payload(
        event=cast(JobDispatchOutboxEvent, event),
        job=cast(AIJob, job),
        output=cast(AIOutput, output),
    )
    assert payload["event_id"] == "00000000-0000-0000-0000-000000000020"
    assert payload["job_id"] == "django-job-1"
    assert payload["metadata"]["request_id"] == job.request_id
    assert payload["metadata"]["usage"]["estimated_cost_usd"] == 0.001
    assert payload["metadata"]["prompt"]["version"] == "1.1.0"


@pytest.mark.parametrize(
    ("code", "message", "retryable"),
    [
        (
            "pdf_ocr_required",
            "This PDF does not contain extractable text and requires OCR.",
            False,
        ),
        ("source_version_changed", "The source changed after this job was created.", False),
        ("provider_timeout", "The AI provider timed out.", True),
        ("provider_rate_limited", "The AI provider is temporarily rate limited.", True),
        (
            "insufficient_source_context",
            "The selected sources do not contain enough relevant material.",
            False,
        ),
        (
            "missing_authoritative_data",
            "There is not enough learner performance data for this analysis.",
            False,
        ),
        (
            "khota_constraint_violation",
            "The requested study-plan constraints cannot be satisfied.",
            False,
        ),
        (
            "empty_transcription",
            "No speech could be transcribed from this audio source.",
            False,
        ),
        ("transcription_failed", "The audio could not be transcribed.", True),
    ],
)
def test_failed_webhook_preserves_safe_domain_error(
    code: str, message: str, retryable: bool
) -> None:
    event = SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000020"))
    job = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
        backend_request_id="backend-job-1",
        status=JobStatus.FAILED,
        error_code=code,
        error_message="secret provider response with source content",
    )

    payload = build_terminal_webhook_payload(
        event=cast(JobDispatchOutboxEvent, event),
        job=cast(AIJob, job),
    )

    assert payload == {
        "event_id": "00000000-0000-0000-0000-000000000020",
        "event_type": "ai.job.failed",
        "job_id": "backend-job-1",
        "status": "failed",
        "error_code": code,
        "error_message": message,
        "retryable": retryable,
    }
    assert "secret provider response" not in str(payload)


def test_unknown_failure_code_uses_safe_generic_message() -> None:
    event = SimpleNamespace(id=uuid.uuid4())
    job = SimpleNamespace(
        id=uuid.uuid4(),
        backend_request_id="backend-job-2",
        status=JobStatus.FAILED,
        error_code="RuntimeError",
        error_message="postgresql://user:password@private-db/source-text",
    )

    payload = build_terminal_webhook_payload(
        event=cast(JobDispatchOutboxEvent, event),
        job=cast(AIJob, job),
    )

    assert payload["error_code"] == "RuntimeError"
    assert payload["error_message"] == "The AI request could not be completed."
    assert payload["retryable"] is False
    assert "private-db" not in str(payload)
