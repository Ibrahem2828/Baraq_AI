from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.schemas.common import StrictModel


class RecommendationPriority(StrEnum):
    NOW = "now"
    THIS_WEEK = "this_week"
    LATER = "later"


class PerformanceMetric(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(max_length=30)
    period: str = Field(max_length=100)
    authoritative: bool = True


class TopicPerformance(StrictModel):
    topic: str = Field(min_length=1, max_length=300)
    score: float = Field(ge=0, le=100)
    answered_questions: int = Field(ge=0)
    average_time_seconds: float | None = Field(default=None, ge=0)


class RasheedRequest(StrictModel):
    metrics: list[PerformanceMetric] = Field(min_length=1, max_length=100)
    topic_performance: list[TopicPerformance] = Field(default_factory=list, max_length=100)
    recent_actions: list[dict[str, str | int | float | bool | None]] = Field(
        default_factory=list, max_length=200
    )
    learner_goal: str | None = Field(default=None, max_length=500)
    language: str = Field(default="ar", pattern="^(ar|en)$")


class RasheedRecommendation(StrictModel):
    title: str = Field(min_length=3, max_length=250)
    action: str = Field(min_length=10, max_length=1000)
    reason: str = Field(min_length=10, max_length=1200)
    priority: RecommendationPriority
    success_measure: str = Field(min_length=5, max_length=500)
    related_topics: list[str] = Field(default_factory=list, max_length=20)


class RasheedResult(StrictModel):
    performance_summary: str = Field(min_length=20, max_length=2500)
    strengths: list[str] = Field(default_factory=list, max_length=20)
    weaknesses: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[RasheedRecommendation] = Field(min_length=1, max_length=12)
    next_best_action: str = Field(min_length=10, max_length=800)
    confidence_note: str = Field(min_length=5, max_length=500)
