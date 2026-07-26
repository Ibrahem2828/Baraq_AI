from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    FAHES_GENERATE_QUIZ = "fahes_generate_quiz"
    KHOTA_GENERATE_PLAN = "khota_generate_plan"
    RASHEED_RECOMMENDATIONS = "rasheed_recommendations"
    KHOLASA_GENERATE_SUMMARY = "kholasa_generate_summary"
    SADA_TRANSCRIBE_AUDIO = "sada_transcribe_audio"


class Character(StrEnum):
    FAHES = "fahes"
    KHOTA = "khota"
    RASHEED = "rasheed"
    KHOLASA = "kholasa"
    SADA = "sada"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRIEVING = "retrieving"
    GENERATING = "generating"
    VALIDATING = "validating"
    MATERIALIZING = "materializing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ProviderAccount(StrEnum):
    PRIMARY = "openai_primary"
    SECONDARY = "openai_secondary"


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
