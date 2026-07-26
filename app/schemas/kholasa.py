from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.schemas.common import Citation, StrictModel


class SummaryLength(StrEnum):
    SHORT = "short"
    MEDIUM = "medium"
    DETAILED = "detailed"


class KholasaRequest(StrictModel):
    source_ids: list[str] = Field(min_length=1, max_length=10)
    summary_length: SummaryLength = SummaryLength.MEDIUM
    focus_topics: list[str] = Field(default_factory=list, max_length=30)
    include_review_questions: bool = True
    include_flashcards: bool = True
    language: str = Field(default="ar", pattern="^(ar|en)$")
    instructions: str | None = Field(default=None, max_length=1000)


class Flashcard(StrictModel):
    front: str = Field(min_length=3, max_length=700)
    back: str = Field(min_length=3, max_length=1200)
    source_references: list[int] = Field(default_factory=list, max_length=5)


class KholasaResult(StrictModel):
    title: str = Field(min_length=3, max_length=300)
    executive_summary: str = Field(min_length=30, max_length=6000)
    detailed_summary: str = Field(min_length=30, max_length=20000)
    key_points: list[str] = Field(min_length=1, max_length=60)
    important_terms: dict[str, str] = Field(default_factory=dict)
    covered_topics: list[str] = Field(default_factory=list, max_length=50)
    review_questions: list[str] = Field(default_factory=list, max_length=30)
    flashcards: list[Flashcard] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    citations: list[Citation] = Field(default_factory=list)
