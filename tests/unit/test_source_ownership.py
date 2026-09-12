from __future__ import annotations

import pytest

from app.core.errors import AuthorizationError
from app.schemas.backend import SourceManifest
from app.services.backend_client import BackendClient


@pytest.mark.asyncio
async def test_backend_client_rejects_manifest_for_another_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BackendClient()

    async def foreign_manifest(*args: object, **kwargs: object) -> SourceManifest:
        return SourceManifest(
            source_id="source-a",
            owner_user_id="user-a",
            title="lesson.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            content_sha256="a" * 64,
        )

    monkeypatch.setattr(client, "_request", foreign_manifest)
    with pytest.raises(AuthorizationError) as error:
        await client.get_source_manifest(source_id="source-a", user_id="user-b")
    assert error.value.code == "source_forbidden"
    await client.aclose()


@pytest.mark.asyncio
async def test_backend_client_rejects_manifest_for_another_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blueprint 02_AI_PLATFORM.md §3.2: user_id alone is not a sufficient
    filter -- a job scoped to project A must not read project B's source
    for the same user."""
    client = BackendClient()

    async def cross_project_manifest(*args: object, **kwargs: object) -> SourceManifest:
        return SourceManifest(
            source_id="source-a",
            owner_user_id="user-a",
            project_id="project-b",
            title="lesson.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            content_sha256="a" * 64,
        )

    monkeypatch.setattr(client, "_request", cross_project_manifest)
    with pytest.raises(AuthorizationError) as error:
        await client.get_source_manifest(
            source_id="source-a", user_id="user-a", project_id="project-a"
        )
    assert error.value.code == "source_project_mismatch"
    await client.aclose()


@pytest.mark.asyncio
async def test_backend_client_skips_project_check_when_caller_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that legitimately has no project (e.g. Rasheed, which has no
    source_ids to protect) must not be blocked by this check."""
    client = BackendClient()

    async def manifest_with_a_project(*args: object, **kwargs: object) -> SourceManifest:
        return SourceManifest(
            source_id="source-a",
            owner_user_id="user-a",
            project_id="project-a",
            title="lesson.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            content_sha256="a" * 64,
        )

    monkeypatch.setattr(client, "_request", manifest_with_a_project)
    manifest = await client.get_source_manifest(source_id="source-a", user_id="user-a")
    assert manifest.project_id == "project-a"
    await client.aclose()
