from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from app.schemas.common import Citation, StrictModel


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StudyTaskType(StrEnum):
    READ = "read"
    REVIEW = "review"
    PRACTICE = "practice"
    QUIZ = "quiz"
    SUMMARIZE = "summarize"


class KhotaRequest(StrictModel):
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    subject_ids: list[str] = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date
    daily_available_minutes: int = Field(ge=20, le=720)
    exam_dates: dict[str, date] = Field(default_factory=dict)
    weak_topics: list[str] = Field(default_factory=list, max_length=50)
    excluded_dates: list[date] = Field(default_factory=list, max_length=60)
    preferred_session_minutes: int = Field(default=45, ge=15, le=180)
    language: str = Field(default="ar", pattern="^(ar|en)$")

    @model_validator(mode="after")
    def validate_dates(self) -> "KhotaRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 180:
            raise ValueError("study plan period cannot exceed 180 days")
        return self


class PlannedTask(StrictModel):
    subject_id: str
    subject_name: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=300)
    task_type: StudyTaskType
    estimated_minutes: int = Field(ge=10, le=240)
    priority: TaskPriority
    reason: str = Field(min_length=10, max_length=800)
    source_ids: list[str] = Field(default_factory=list, max_length=10)


class PlanDay(StrictModel):
    date: date
    total_minutes: int = Field(ge=0, le=720)
    tasks: list[PlannedTask] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_total(self) -> "PlanDay":
        calculated = sum(task.estimated_minutes for task in self.tasks)
        if self.total_minutes != calculated:
            raise ValueError("total_minutes must equal the sum of task durations")
        return self


class KhotaResult(StrictModel):
    title: str = Field(min_length=3, max_length=300)
    strategy_summary: str = Field(min_length=20, max_length=2500)
    plan_days: list[PlanDay] = Field(min_length=1, max_length=181)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    adaptation_rules: list[str] = Field(default_factory=list, max_length=20)
    citations: list[Citation] = Field(default_factory=list)
