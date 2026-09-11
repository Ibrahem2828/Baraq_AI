"""Spec section 17 acceptance metrics for the deterministic Khota scheduler:
0 hard-constraint violations, 0 invalid dates, 0 daily-budget overflow, and
100% deterministic replay for the same inputs. A manual sweep over many
combinations stands in for a full property-based suite (hypothesis is not a
project dependency yet) but exercises the same invariants across a wide
parameter grid.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import product

from app.schemas.khota import KhotaRequest
from app.services.khota_scheduler import build_plan_days, validate_hard_constraints

START = date(2026, 9, 1)


def _request(
    *,
    span_days: int,
    daily_minutes: int,
    session_minutes: int,
    excluded_offsets: list[int],
    subject_count: int,
) -> KhotaRequest:
    end = START + timedelta(days=span_days - 1)
    excluded = [START + timedelta(days=offset) for offset in excluded_offsets if offset < span_days]
    return KhotaRequest(
        subject_ids=[f"subject-{i}" for i in range(subject_count)],
        start_date=START,
        end_date=end,
        daily_available_minutes=daily_minutes,
        excluded_dates=excluded,
        preferred_session_minutes=session_minutes,
    )


def test_scheduler_never_violates_hard_constraints_across_a_parameter_grid() -> None:
    grid = product(
        [1, 3, 7, 14],  # span_days
        [20, 45, 120, 240],  # daily_minutes
        [15, 30, 60],  # session_minutes
        [[], [1], [0, 2]],  # excluded_offsets
        [1, 2, 4],  # subject_count
    )
    checked = 0
    for span_days, daily_minutes, session_minutes, excluded_offsets, subject_count in grid:
        request = _request(
            span_days=span_days,
            daily_minutes=daily_minutes,
            session_minutes=session_minutes,
            excluded_offsets=excluded_offsets,
            subject_count=subject_count,
        )
        plan_days = build_plan_days(request, topics=[])
        validate_hard_constraints(request, plan_days)  # raises on any violation
        checked += 1
    assert checked > 100


def test_scheduler_is_deterministic_for_the_same_inputs() -> None:
    request = _request(
        span_days=10, daily_minutes=90, session_minutes=30, excluded_offsets=[2, 5], subject_count=3
    )
    first = build_plan_days(request, topics=["a", "b"])
    second = build_plan_days(request, topics=["a", "b"])
    assert [day.model_dump(mode="json") for day in first] == [
        day.model_dump(mode="json") for day in second
    ]


def test_scheduler_produces_zero_days_for_a_fully_excluded_range() -> None:
    request = _request(
        span_days=3,
        daily_minutes=60,
        session_minutes=30,
        excluded_offsets=[0, 1, 2],
        subject_count=1,
    )
    plan_days = build_plan_days(request, topics=[])
    assert plan_days == []
