from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class LabWorkspace:
    workspace_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LabSource:
    source_id: str
    workspace_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content_sha256: str
    status: str
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LabChunk:
    chunk_id: str
    source_id: str
    chunk_index: int
    page_number: int | None
    section_title: str | None
    text: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class LabJob:
    job_id: str
    workspace_id: str
    task_type: str
    status: str
    request_json: dict[str, Any]
    result_json: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    progress_percent: int
    progress_message: str
    created_at: datetime
    updated_at: datetime
