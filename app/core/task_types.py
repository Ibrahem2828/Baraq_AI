"""Canonical task-type compatibility at the Django gateway boundary.

Only canonical values may be persisted for newly created jobs. The aliases
below are accepted temporarily so Django can roll out independently.
"""

from __future__ import annotations

import logging
import warnings

from app.models.enums import TaskType

logger = logging.getLogger(__name__)

OFFICIAL_TASK_TYPE_VALUES = frozenset(item.value for item in TaskType)
LEGACY_TASK_TYPE_MAP = {
    "rasheed_recommend": TaskType.RASHEED_RECOMMENDATIONS.value,
    "kholasa_summarize": TaskType.KHOLASA_GENERATE_SUMMARY.value,
    "sada_transcribe": TaskType.SADA_TRANSCRIBE_AUDIO.value,
}
# Coordinated removal with Django: 2026-10-24 (90 days after Phase 1 start).
LEGACY_TASK_TYPE_REMOVAL_DATE = "2026-10-24"


def normalize_task_type(value: object) -> str:
    """Return an official task type and warn when a temporary alias is used."""
    raw_value = value.value if isinstance(value, TaskType) else str(value)
    normalized = LEGACY_TASK_TYPE_MAP.get(raw_value)
    if normalized is None:
        return raw_value

    logger.warning(
        "legacy_task_type_used",
        extra={
            "legacy_task_type": raw_value,
            "canonical_task_type": normalized,
            "removal_date": LEGACY_TASK_TYPE_REMOVAL_DATE,
        },
    )
    warnings.warn(
        (
            f"Task type '{raw_value}' is deprecated; use '{normalized}'. "
            f"Support will be removed on {LEGACY_TASK_TYPE_REMOVAL_DATE}."
        ),
        DeprecationWarning,
        stacklevel=2,
    )
    return normalized
