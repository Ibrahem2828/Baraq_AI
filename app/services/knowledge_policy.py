"""Explicit data-authority policy for every Baraq character."""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import TaskType


class KnowledgePolicy(StrEnum):
    SELECTED_SOURCES_ONLY = "selected_sources_only"
    AUTHORITATIVE_CONSTRAINTS = "authoritative_constraints"
    AUTHORITATIVE_DATA_ONLY = "authoritative_data_only"
    TRANSCRIPT_PRESERVATION = "transcript_preservation"


TASK_KNOWLEDGE_POLICY = {
    TaskType.FAHES_GENERATE_QUIZ: KnowledgePolicy.SELECTED_SOURCES_ONLY,
    TaskType.KHOLASA_GENERATE_SUMMARY: KnowledgePolicy.SELECTED_SOURCES_ONLY,
    TaskType.KHOTA_GENERATE_PLAN: KnowledgePolicy.AUTHORITATIVE_CONSTRAINTS,
    TaskType.RASHEED_RECOMMENDATIONS: KnowledgePolicy.AUTHORITATIVE_DATA_ONLY,
    TaskType.SADA_TRANSCRIBE_AUDIO: KnowledgePolicy.TRANSCRIPT_PRESERVATION,
}
