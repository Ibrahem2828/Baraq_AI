"""Deterministic Khota scheduler (spec section 17).

The actual day/task schedule is never produced by an LLM. This module is the
single source of truth for it, shared by the production pipeline
(``app/pipelines/khota.py``) and the standalone Lab
(``app/application/standalone.py``) so both enforce the same hard constraints
and produce the same plan for the same inputs (spec: "Deterministic replay:
100% for the same seed/inputs").
"""

from __future__ import annotations

from datetime import timedelta

from app.schemas.khota import KhotaRequest, PlanDay, PlannedTask, StudyTaskType, TaskPriority


def priority_scores(request: KhotaRequest) -> dict[str, float]:
    scores: dict[str, float] = {}
    for subject_id in request.subject_ids:
        score = 1.0
        exam = request.exam_dates.get(subject_id)
        if exam:
            days_to_exam = max(0, (exam - request.start_date).days)
            score += max(0.0, (30 - min(days_to_exam, 30)) / 10)
        weak_matches = sum(1 for topic in request.weak_topics if subject_id in topic)
        score += weak_matches * 0.5
        scores[subject_id] = round(score, 2)
    return scores


def build_plan_days(request: KhotaRequest, *, topics: list[str]) -> list[PlanDay]:
    """Pure function: same request + topics always yields the same plan."""
    resolved_topics = topics or request.weak_topics
    days: list[PlanDay] = []
    excluded = set(request.excluded_dates)
    cursor = request.start_date
    topic_index = 0
    while cursor <= request.end_date:
        if cursor not in excluded:
            tasks: list[PlannedTask] = []
            remaining = request.daily_available_minutes
            session_no = 0
            while remaining >= 10:
                subject = request.subject_ids[
                    (len(days) + session_no) % len(request.subject_ids)
                ]
                name = request.subject_names.get(subject, subject)
                # With no topic to go on, the task is a review of its own subject.
                topic = (
                    resolved_topics[topic_index % len(resolved_topics)]
                    if resolved_topics
                    else f"مراجعة {name}"
                )
                minutes = min(request.preferred_session_minutes, remaining)
                exam = request.exam_dates.get(subject)
                priority = (
                    TaskPriority.HIGH
                    if exam and (exam - cursor).days <= 14
                    else TaskPriority.MEDIUM
                )
                # First pass over the source's topics is study, later passes
                # review; with no topics at all every session reviews a subject.
                first_pass = bool(resolved_topics) and topic_index < len(resolved_topics)
                task_type = StudyTaskType.READ if first_pass else StudyTaskType.REVIEW
                tasks.append(
                    PlannedTask(
                        subject_id=subject,
                        subject_name=name,
                        topic=topic,
                        task_type=task_type,
                        estimated_minutes=minutes,
                        priority=priority,
                        reason=_reason(
                            task_type, topic if resolved_topics else name, request.language
                        ),
                        source_ids=request.source_ids,
                    )
                )
                remaining -= minutes
                session_no += 1
                topic_index += 1
            days.append(
                PlanDay(
                    date=cursor,
                    total_minutes=sum(task.estimated_minutes for task in tasks),
                    tasks=tasks,
                )
            )
        cursor += timedelta(days=1)
    return days


def validate_hard_constraints(request: KhotaRequest, plan_days: list[PlanDay]) -> None:
    """Defensive re-check. ``build_plan_days`` cannot violate these by
    construction, but pipelines re-validate so a future scheduler change
    fails loudly instead of silently shipping an invalid plan."""
    excluded = set(request.excluded_dates)
    seen_dates: set[object] = set()
    for day in plan_days:
        if day.date < request.start_date or day.date > request.end_date:
            raise ValueError("Plan contains a date outside the requested range")
        if day.date in excluded:
            raise ValueError("Plan contains an excluded date")
        if day.total_minutes > request.daily_available_minutes:
            raise ValueError("Plan exceeds the learner daily time limit")
        if day.date in seen_dates:
            raise ValueError("Plan contains duplicate days")
        seen_dates.add(day.date)


def _reason(task_type: StudyTaskType, topic: str, language: str) -> str:
    """Why this session exists, in the learner's language."""
    if language == "en":
        if task_type == StudyTaskType.READ:
            return f"First pass: study {topic} and note its key ideas."
        return f"Review {topic} to consolidate it and find what still needs work."
    if task_type == StudyTaskType.READ:
        return f"دراسة {topic} وفهم أفكاره الأساسية لأول مرة."
    return f"مراجعة {topic} لتثبيته ومعرفة ما يحتاج مزيدًا من التركيز."
