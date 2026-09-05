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
