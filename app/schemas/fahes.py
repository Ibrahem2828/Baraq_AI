from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.schemas.common import Citation, StrictModel


class QuestionDifficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuestionKind(StrEnum):
    MCQ = "mcq"
    TRUE_FALSE = "true_false"


class FahesRequest(StrictModel):
    source_ids: list[str] = Field(min_length=1, max_length=10)
    subject_id: str | None = None
    topic: str | None = Field(default=None, max_length=300)
    question_count: int = Field(default=10, ge=3, le=50)
    difficulty: QuestionDifficulty | None = None
    question_types: list[QuestionKind] = Field(
        default_factory=lambda: [QuestionKind.MCQ, QuestionKind.TRUE_FALSE], min_length=1
    )
    language: str = Field(default="ar", pattern="^(ar|en)$")
    instructions: str | None = Field(default=None, max_length=1000)


class FahesQuestion(StrictModel):
    question_type: QuestionKind
    question: str = Field(min_length=10, max_length=1200)
    choices: list[str] = Field(min_length=2, max_length=6)
    correct_answer_index: int = Field(ge=0, le=5)
    explanation: str = Field(min_length=10, max_length=1800)
    difficulty: QuestionDifficulty
    topic: str = Field(min_length=1, max_length=300)
    source_references: list[int] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_question(self) -> FahesQuestion:
        if self.correct_answer_index >= len(self.choices):
            raise ValueError("correct_answer_index must reference an existing choice")
        normalized = [choice.strip().casefold() for choice in self.choices]
        if len(normalized) != len(set(normalized)):
            raise ValueError("choices must be unique")
        if self.question_type == QuestionKind.TRUE_FALSE and len(self.choices) != 2:
            raise ValueError("true_false questions must have exactly two choices")
        return self


class FahesResult(StrictModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=1000)
    questions: list[FahesQuestion] = Field(min_length=1, max_length=50)
    covered_topics: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    citations: list[Citation] = Field(default_factory=list)
