"""Ingestion reuse and the concurrent-claim race.

Expensive work -- download, extract, chunk, embed -- must happen once per
source *version*, and two characters launched together on the same
freshly-uploaded source must not fail one of them with a database error for a
race that is entirely expected.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationFailure
from app.models.enums import SourceStatus
from app.schemas.backend import SourceManifest
from app.services.source_ingestion import SourceIngestionService

SHA = "a" * 64


def _manifest(**overrides: Any) -> SourceManifest:
    values: dict[str, Any] = {
        "source_id": "source-1",
        "owner_user_id": "user-a",
        "project_id": "project-a",
        "title": "notes.txt",
        "mime_type": "text/plain",
        "size_bytes": 11,
        "content_sha256": SHA,
    }
    values.update(overrides)
    return SourceManifest(**values)


class _Document:
    def __init__(self, status: SourceStatus = SourceStatus.READY) -> None:
        self.status = status
        self.extraction_error: str | None = None
        self.page_count: int | None = None
        self.id = "doc-1"


class _Backend:
    def __init__(self, manifest: SourceManifest) -> None:
        self.manifest = manifest
        self.manifest_calls = 0
        self.download_calls = 0

    async def get_source_manifest(self, **_: Any) -> SourceManifest:
        self.manifest_calls += 1
        return self.manifest

    async def download_source(self, **_: Any) -> bytes:
        self.download_calls += 1
        return b"Newton laws"


def _service(*, backend: _Backend) -> SourceIngestionService:
    service = SourceIngestionService.__new__(SourceIngestionService)
    service.backend = backend  # type: ignore[assignment]
    return service


@pytest.mark.asyncio
async def test_a_ready_document_is_reused_without_downloading_again() -> None:
    """The reuse property: Kholasa after Fahes on the same unchanged source
    must not repeat the download/extract/embed work."""
    backend = _Backend(_manifest())
    service = _service(backend=backend)

    class _Repository:
        async def get_document_version(self, **_: Any) -> _Document:
            return _Document(status=SourceStatus.READY)

    service.repository = _Repository()  # type: ignore[assignment]

    result = await service.ensure_ingested(
        source_id="source-1", user_id="user-a", project_id="project-a"
    )

    assert result.content_sha256 == SHA
    assert backend.download_calls == 0, "a ready document was re-downloaded"


@pytest.mark.asyncio
async def test_a_changed_source_is_not_served_from_the_previous_version() -> None:
    """The document lookup keys on the content hash, so an edited source
    misses the cache rather than returning the old index."""
    backend = _Backend(_manifest(content_sha256="b" * 64))
    service = _service(backend=backend)
    seen: dict[str, Any] = {}

    class _Repository:
        async def get_document_version(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            return None

    service.repository = _Repository()  # type: ignore[assignment]

    # Stops at the size check with no settings configured; the lookup that
    # runs first is what is under test.
    with pytest.raises((AttributeError, ValidationFailure)):
        await service.ensure_ingested(
            source_id="source-1", user_id="user-a", project_id="project-a"
        )

    assert seen["content_sha256"] == "b" * 64
    assert seen["user_id"] == "user-a"
    assert seen["project_id"] == "project-a"


@pytest.mark.asyncio
async def test_a_version_mismatch_is_refused_before_any_work() -> None:
    """A job pins the hash captured at creation. If the source changed since,
    the job must fail rather than silently run on different material."""
    backend = _Backend(_manifest(content_sha256="b" * 64))
    service = _service(backend=backend)

    with pytest.raises(ValidationFailure) as caught:
        await service.ensure_ingested(
            source_id="source-1",
            user_id="user-a",
            project_id="project-a",
            expected_content_sha256=SHA,
        )

    assert caught.value.code == "source_version_changed"
    assert backend.download_calls == 0


@pytest.mark.asyncio
async def test_the_loser_of_a_concurrent_claim_adopts_the_winners_document() -> None:
    """Two characters launched together on the same new source.

    A unique constraint already stops the duplicate row; the losing INSERT
    used to surface as a raw IntegrityError and fail that learner's job with
    an opaque database error.
    """
    backend = _Backend(_manifest())
    service = _service(backend=backend)
    winner = _Document(status=SourceStatus.EXTRACTING)

    class _Repository:
        async def create_document(self, **_: Any) -> None:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        async def get_document_version(self, **_: Any) -> _Document:
            return winner

    class _NestedTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_: Any) -> bool:
            return False

    class _Session:
        def begin_nested(self) -> _NestedTransaction:
            return _NestedTransaction()

    service.repository = _Repository()  # type: ignore[assignment]
    service.session = _Session()  # type: ignore[assignment]

    adopted = await service._claim_document(
        manifest=_manifest(), user_id="user-a", project_id="project-a"
    )

    # `is` would be a non-overlapping identity check to mypy, since the
    # real return type is SourceDocument and this is a double.
    assert adopted == winner


@pytest.mark.asyncio
async def test_an_unrelated_integrity_error_is_not_swallowed() -> None:
    """Adopting a peer is only correct when a peer actually exists. Any other
    constraint violation must still surface."""
    service = _service(backend=_Backend(_manifest()))

    class _Repository:
        async def create_document(self, **_: Any) -> None:
            raise IntegrityError("INSERT", {}, Exception("some other constraint"))

        async def get_document_version(self, **_: Any) -> None:
            return None

    class _NestedTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_: Any) -> bool:
            return False

    class _Session:
        def begin_nested(self) -> _NestedTransaction:
            return _NestedTransaction()

    service.repository = _Repository()  # type: ignore[assignment]
    service.session = _Session()  # type: ignore[assignment]

    with pytest.raises(IntegrityError):
        await service._claim_document(
            manifest=_manifest(), user_id="user-a", project_id="project-a"
        )
