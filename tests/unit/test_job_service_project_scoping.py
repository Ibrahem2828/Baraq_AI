from __future__ import annotations

import pytest

from app.core.errors import ValidationFailure
from app.models.enums import TaskType
from app.schemas.backend import SourceManifest
from app.services.job_service import JobService


class _FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def get_source_manifest(
        self, *, source_id: str, user_id: str, project_id: str | None = None
    ) -> SourceManifest:
        self.calls.append(
            {"source_id": source_id, "user_id": user_id, "project_id": project_id}
        )
        return SourceManifest(
            source_id=source_id,
            owner_user_id=user_id,
            project_id=project_id,
            title="lesson.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            content_sha256="a" * 64,
        )


@pytest.mark.asyncio
async def test_freeze_source_versions_rejects_a_sourced_request_with_no_project() -> None:
    """Blueprint 02_AI_PLATFORM.md §3.2: 'every task that depends on sources
    carries a project_id' -- enforced here, before any durable job row or
    backend round-trip for source content."""
    with pytest.raises(ValidationFailure) as error:
        await JobService.freeze_source_versions(
            user_id="user-1",
            project_id=None,
            task_type=TaskType.FAHES_GENERATE_QUIZ,
            payload={"source_ids": ["source-1"]},
            backend=_FakeBackend(),  # type: ignore[arg-type]
        )
    assert error.value.code == "project_id_required"


@pytest.mark.asyncio
async def test_freeze_source_versions_allows_no_project_when_there_are_no_sources() -> None:
    """Rasheed has no source_ids to protect -- must not be blocked."""
    versions = await JobService.freeze_source_versions(
        user_id="user-1",
        project_id=None,
        task_type=TaskType.RASHEED_RECOMMENDATIONS,
        payload={},
        backend=_FakeBackend(),  # type: ignore[arg-type]
    )
    assert versions == {}


@pytest.mark.asyncio
async def test_freeze_source_versions_passes_project_id_through_to_the_backend() -> None:
    backend = _FakeBackend()
    versions = await JobService.freeze_source_versions(
        user_id="user-1",
        project_id="project-1",
        task_type=TaskType.FAHES_GENERATE_QUIZ,
        payload={"source_ids": ["source-1"]},
        backend=backend,  # type: ignore[arg-type]
    )
    assert versions == {"source-1": "a" * 64}
    assert backend.calls == [
        {"source_id": "source-1", "user_id": "user-1", "project_id": "project-1"}
    ]
