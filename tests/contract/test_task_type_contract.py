from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from app.core.task_types import (
    LEGACY_TASK_TYPE_MAP,
    LEGACY_TASK_TYPE_REMOVAL_DATE,
    OFFICIAL_TASK_TYPE_VALUES,
)
from app.schemas.jobs import DjangoJobCreateRequest


@pytest.mark.parametrize("legacy, canonical", LEGACY_TASK_TYPE_MAP.items())
def test_legacy_task_type_is_normalized_with_deprecation_warning(
    legacy: str, canonical: str
) -> None:
    with pytest.warns(DeprecationWarning, match="deprecated"):
        request = DjangoJobCreateRequest(
            client_job_id="django-job-001",
            user_id="user-001",
            task_type=legacy,
            input={"source_ids": ["source-001"]},
        )
    assert request.task_type.value == canonical
    assert request.task_type.value in OFFICIAL_TASK_TYPE_VALUES


@pytest.mark.parametrize("task_type", sorted(OFFICIAL_TASK_TYPE_VALUES))
def test_official_task_types_are_accepted_without_deprecation_warning(task_type: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        request = DjangoJobCreateRequest(
            client_job_id="django-job-002",
            user_id="user-002",
            task_type=task_type,
            input={},
        )
    assert request.task_type.value == task_type


def test_unsupported_task_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DjangoJobCreateRequest(
            client_job_id="django-job-003",
            user_id="user-003",
            task_type="source_ingest",
            input={},
        )


def test_legacy_removal_date_is_explicit_and_future_for_phase_one() -> None:
    assert LEGACY_TASK_TYPE_REMOVAL_DATE == "2026-10-24"
