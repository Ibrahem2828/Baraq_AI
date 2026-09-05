from __future__ import annotations

import json
import mimetypes
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailure
from app.lab.models import LabChunk, LabJob, LabSource, LabWorkspace
from app.rag.chunker import ArabicAwareChunker
from app.rag.extractors import DocumentExtractor
from app.utils.hash import sha256_bytes

_ALLOWED_SUFFIXES = {".pdf", ".docx", ".pptx", ".txt", ".md", ".mp3", ".wav", ".m4a"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a"}
_MIME_BY_SUFFIX = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
    ".wav": {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"},
    ".m4a": {"audio/mp4", "audio/x-m4a", "application/octet-stream"},
}


class LabStorage:
    """Local Lab workspace storage; never used by the production service mode."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.lab_storage_dir.resolve()
        self.uploads_dir = self.root / "uploads"
        self.database_path = self.root / "lab.db"
        self.extractor = DocumentExtractor()
        self.chunker = ArabicAwareChunker(
            chunk_size=settings.rag_chunk_size_chars,
            overlap=settings.rag_chunk_overlap_chars,
        )

    def initialize(self) -> None:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS lab_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_sources (
                    source_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES lab_workspaces(workspace_id)
                        ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, content_sha256)
                );
                CREATE TABLE IF NOT EXISTS lab_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES lab_sources(source_id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    page_number INTEGER,
                    section_title TEXT,
                    text TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    UNIQUE(source_id, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS lab_jobs (
                    job_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES lab_workspaces(workspace_id)
                        ON DELETE CASCADE,
                    task_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    progress_percent INTEGER NOT NULL DEFAULT 0,
                    progress_message TEXT NOT NULL DEFAULT 'Queued',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_quiz_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES lab_workspaces(workspace_id)
                        ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES lab_jobs(job_id) ON DELETE CASCADE,
                    answers_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES lab_workspaces(workspace_id)
                        ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES lab_jobs(job_id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL,
                    flags_json TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._add_column_if_missing(
                connection, "lab_jobs", "progress_percent", "INTEGER NOT NULL DEFAULT 0"
            )
            self._add_column_if_missing(
                connection, "lab_jobs", "progress_message", "TEXT NOT NULL DEFAULT 'Queued'"
            )

    def ensure_workspace(self, workspace_id: str | None = None) -> LabWorkspace:
        self.initialize()
        workspace_id = workspace_id or str(uuid.uuid4())
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO lab_workspaces(workspace_id, created_at) VALUES (?, ?)",
                (workspace_id, now),
            )
            row = connection.execute(
                "SELECT workspace_id, created_at FROM lab_workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("workspace creation failed")
        return LabWorkspace(
            workspace_id=row["workspace_id"], created_at=self._parse_time(row["created_at"])
        )

    def store_upload(
        self, *, workspace_id: str, filename: str, mime_type: str | None, content: bytes
    ) -> LabSource:
        self.ensure_workspace(workspace_id)
        suffix = Path(filename).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValidationFailure("Unsupported file type", code="lab_unsupported_file")
        resolved_mime = (
            mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        ).lower()
        if resolved_mime not in _MIME_BY_SUFFIX[suffix]:
            raise ValidationFailure(
                "File extension and MIME type do not match", code="lab_mime_mismatch"
            )
        max_bytes = self.settings.lab_max_upload_mb * 1024 * 1024
        if not content:
            raise ValidationFailure("The uploaded file is empty", code="lab_empty_upload")
        if len(content) > max_bytes:
            raise ValidationFailure(
                "The uploaded file exceeds the Lab limit", code="lab_upload_too_large"
            )
        content_hash = sha256_bytes(content)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM lab_sources WHERE workspace_id = ? AND content_sha256 = ?",
                (workspace_id, content_hash),
            ).fetchone()
        if existing is not None:
            return self._source_from_row(existing)

        source_id = str(uuid.uuid4())
        safe_filename = f"{source_id}{suffix}"
        path = self.uploads_dir / safe_filename
        metadata: dict[str, Any] = {"storage_name": safe_filename, "original_filename": filename}
        chunks: list[LabChunk] = []
        status = "ready"
        if suffix not in _AUDIO_SUFFIXES:
            extracted = self.extractor.extract(
                filename=filename,
                mime_type=resolved_mime,
                content=content,
            )
            if len(extracted.full_text) > self.settings.lab_max_text_chars:
                raise ValidationFailure(
                    "Extracted text exceeds the Lab limit", code="lab_text_too_large"
                )
            metadata.update(extracted.metadata)
            chunks = [
                LabChunk(
                    chunk_id=str(uuid.uuid4()),
                    source_id=source_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    text=chunk.text,
                    content_sha256=content_hash,
                )
                for chunk in self.chunker.chunk(extracted)
            ]
            if not chunks:
                raise ValidationFailure(
                    "No usable study chunks were extracted", code="lab_empty_source"
                )
        else:
            status = "audio_ready"
            metadata["audio"] = True
        now = self._now()
        try:
            path.write_bytes(content)
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO lab_sources(
                        source_id, workspace_id, filename, mime_type, size_bytes,
                        content_sha256, status, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        workspace_id,
                        filename,
                        resolved_mime,
                        len(content),
                        content_hash,
                        status,
                        self._dump(metadata),
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO lab_chunks(
                        chunk_id, source_id, chunk_index, page_number, section_title, text,
                        content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.source_id,
                            chunk.chunk_index,
                            chunk.page_number,
                            chunk.section_title,
                            chunk.text,
                            chunk.content_sha256,
                        )
                        for chunk in chunks
                    ],
                )
        except Exception:
            if path.exists():
                path.unlink()
            raise
        return self.get_source(source_id=source_id, workspace_id=workspace_id)

    def read_source_bytes(self, *, workspace_id: str, source_id: str) -> bytes:
        source = self.get_source(source_id=source_id, workspace_id=workspace_id)
        storage_name = str(source.metadata.get("storage_name") or "")
        path = (self.uploads_dir / storage_name).resolve()
        if path.parent != self.uploads_dir.resolve() or not path.is_file():
            raise NotFoundError("Lab source file not found")
        return path.read_bytes()

    def get_source(self, *, source_id: str, workspace_id: str) -> LabSource:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lab_sources WHERE source_id = ? AND workspace_id = ?",
                (source_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Lab source not found")
        return self._source_from_row(row)

    def list_sources(self, *, workspace_id: str) -> list[LabSource]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM lab_sources WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        return [self._source_from_row(row) for row in rows]

    def list_chunks(self, *, workspace_id: str, source_ids: list[str]) -> list[LabChunk]:
        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        query = f"""
            SELECT chunk.* FROM lab_chunks chunk
            JOIN lab_sources source ON source.source_id = chunk.source_id
            WHERE source.workspace_id = ? AND chunk.source_id IN ({placeholders})
            ORDER BY chunk.source_id, chunk.chunk_index
        """
        with self._connect() as connection:
            rows = connection.execute(query, [workspace_id, *source_ids]).fetchall()
        return [
            LabChunk(
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                chunk_index=row["chunk_index"],
                page_number=row["page_number"],
                section_title=row["section_title"],
                text=row["text"],
                content_sha256=row["content_sha256"],
            )
            for row in rows
        ]

    def create_job(self, *, workspace_id: str, task_type: str, request: dict[str, Any]) -> LabJob:
        self.ensure_workspace(workspace_id)
        job_id = str(uuid.uuid4())
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lab_jobs(
                    job_id, workspace_id, task_type, status, request_json, created_at, updated_at
                )
                VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, workspace_id, task_type, self._dump(request), now, now),
            )
        return self.get_job(job_id=job_id, workspace_id=workspace_id)

    def update_job(
        self,
        *,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        progress_percent: int | None = None,
        progress_message: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lab_jobs
                SET status = ?, result_json = ?, error_code = ?, error_message = ?,
                    progress_percent = COALESCE(?, progress_percent),
                    progress_message = COALESCE(?, progress_message), updated_at = ?
                WHERE job_id = ?
                """,
                (
                    status,
                    self._dump(result) if result is not None else None,
                    error_code,
                    error_message,
                    progress_percent,
                    progress_message,
                    self._now(),
                    job_id,
                ),
            )

    def get_job(self, *, job_id: str, workspace_id: str) -> LabJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lab_jobs WHERE job_id = ? AND workspace_id = ?",
                (job_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Lab job not found")
        return LabJob(
            job_id=row["job_id"],
            workspace_id=row["workspace_id"],
            task_type=row["task_type"],
            status=row["status"],
            request_json=self._load(row["request_json"]),
            result_json=self._load(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            progress_percent=int(row["progress_percent"]),
            progress_message=str(row["progress_message"]),
            created_at=self._parse_time(row["created_at"]),
            updated_at=self._parse_time(row["updated_at"]),
        )

    def list_jobs(self, *, workspace_id: str, limit: int = 20) -> list[LabJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM lab_jobs WHERE workspace_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [
            LabJob(
                job_id=row["job_id"],
                workspace_id=row["workspace_id"],
                task_type=row["task_type"],
                status=row["status"],
                request_json=self._load(row["request_json"]),
                result_json=self._load(row["result_json"]) if row["result_json"] else None,
                error_code=row["error_code"],
                error_message=row["error_message"],
                progress_percent=int(row["progress_percent"]),
                progress_message=str(row["progress_message"]),
                created_at=self._parse_time(row["created_at"]),
                updated_at=self._parse_time(row["updated_at"]),
            )
            for row in rows
        ]

    def record_feedback(
        self, *, workspace_id: str, job_id: str, rating: int, flags: list[str], notes: str | None
    ) -> None:
        if rating < 1 or rating > 5:
            raise ValidationFailure("Rating must be between 1 and 5", code="lab_invalid_rating")
        self.get_job(job_id=job_id, workspace_id=workspace_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lab_feedback(
                    feedback_id, workspace_id, job_id, rating, flags_json, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    workspace_id,
                    job_id,
                    rating,
                    self._dump({"flags": flags}),
                    notes,
                    self._now(),
                ),
            )

    def record_quiz_attempt(
        self, *, workspace_id: str, job_id: str, answers: dict[str, int], metrics: dict[str, Any]
    ) -> None:
        self.get_job(job_id=job_id, workspace_id=workspace_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lab_quiz_attempts(
                    attempt_id, workspace_id, job_id, answers_json, metrics_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    workspace_id,
                    job_id,
                    self._dump({"answers": answers}),
                    self._dump(metrics),
                    self._now(),
                ),
            )

    def delete_workspace(self, *, workspace_id: str) -> None:
        sources = self.list_sources(workspace_id=workspace_id)
        for source in sources:
            storage_name = str(source.metadata.get("storage_name") or "")
            path = (self.uploads_dir / storage_name).resolve()
            if path.parent == self.uploads_dir.resolve() and path.exists():
                path.unlink()
        with self._connect() as connection:
            connection.execute("DELETE FROM lab_feedback WHERE workspace_id = ?", (workspace_id,))
            connection.execute(
                "DELETE FROM lab_quiz_attempts WHERE workspace_id = ?", (workspace_id,)
            )
            connection.execute("DELETE FROM lab_jobs WHERE workspace_id = ?", (workspace_id,))
            connection.execute(
                "DELETE FROM lab_chunks WHERE source_id IN ("
                "SELECT source_id FROM lab_sources WHERE workspace_id = ?)",
                (workspace_id,),
            )
            connection.execute("DELETE FROM lab_sources WHERE workspace_id = ?", (workspace_id,))
            connection.execute("DELETE FROM lab_workspaces WHERE workspace_id = ?", (workspace_id,))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        names = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in names:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)

    @staticmethod
    def _dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: str) -> dict[str, Any]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("Lab JSON record must be an object")
        return decoded

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> LabSource:
        return LabSource(
            source_id=row["source_id"],
            workspace_id=row["workspace_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            content_sha256=row["content_sha256"],
            status=row["status"],
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
