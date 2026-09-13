from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    FAHES_GENERATE_QUIZ = "fahes_generate_quiz"
    KHOTA_GENERATE_PLAN = "khota_generate_plan"
    RASHEED_RECOMMENDATIONS = "rasheed_recommendations"
    KHOLASA_GENERATE_SUMMARY = "kholasa_generate_summary"
    SADA_TRANSCRIBE_AUDIO = "sada_transcribe_audio"


# Celery queue each task type's ``process_ai_job`` dispatch is routed to (see
# ``app/workers/celery_app.py``'s ``task_routes`` and
# ``JobDispatchOutboxEvent.target_queue``). Sada audio transcription is
# genuinely heavier/slower than the other four (near-real-time) text tasks,
# so it gets its own queue -- a burst of long transcriptions can no longer
# delay an interactive fahes/khota/rasheed/kholasa job behind it.
TASK_QUEUE: dict[TaskType, str] = {
    TaskType.FAHES_GENERATE_QUIZ: "ai_interactive",
    TaskType.KHOTA_GENERATE_PLAN: "ai_interactive",
    TaskType.RASHEED_RECOMMENDATIONS: "ai_interactive",
    TaskType.KHOLASA_GENERATE_SUMMARY: "ai_interactive",
    TaskType.SADA_TRANSCRIBE_AUDIO: "ai_audio",
}


class Character(StrEnum):
    FAHES = "fahes"
    KHOTA = "khota"
    RASHEED = "rasheed"
    KHOLASA = "kholasa"
    SADA = "sada"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RETRIEVING = "retrieving"
    PLANNING = "planning"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    MATERIALIZING = "materializing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class DispatchOutboxStatus(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"


class Provider(StrEnum):
    """Approved provider families. No other provider may appear here."""

    GEMINI = "gemini"
    OPENAI = "openai"
    MOCK = "mock"
    REPLAY = "replay"


class ProviderAccount(StrEnum):
    PRIMARY = "openai_primary"
    SECONDARY = "openai_secondary"
    GEMINI_PRIMARY = "gemini_primary"
    MOCK = "mock"
    REPLAY = "replay"


class QualityTier(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH_QUALITY = "high_quality"


class ProviderAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SourceStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class FeedbackIssue(StrEnum):
    INCORRECT = "incorrect"
    NOT_GROUNDED = "not_grounded"
    UNCLEAR = "unclear"
    TOO_EASY = "too_easy"
    TOO_HARD = "too_hard"
    TOO_LONG = "too_long"
    TOO_SHORT = "too_short"
    MISSING_CONTENT = "missing_content"
    BAD_ARABIC = "bad_arabic"
    OTHER = "other"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    APPROVED = "approved"
    EXPORTED = "exported"
