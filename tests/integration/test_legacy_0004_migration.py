"""PostgreSQL regression coverage for the real pre-0005 production schema.

The initial migration builds from current ORM metadata, so replaying it does not
represent a database that was genuinely created at revision 0004.  This test
therefore creates the relevant legacy objects explicitly, stamps only the
isolated test database at 0004, and lets Alembic execute every later migration.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy.engine import URL, make_url

LEGACY_REVISION = "0004_final_runtime_delivery"
# Whatever the newest migration is: a pinned name broke this test each time
# a migration was added, without anything being wrong with the upgrade.
HEAD_REVISION = ScriptDirectory.from_config(
    Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
).get_current_head()


def _postgres_url() -> URL:
    raw_url = os.environ.get("DATABASE_SYNC_URL", "")
    if not raw_url.startswith("postgresql"):
        pytest.skip("DATABASE_SYNC_URL does not select PostgreSQL")
    return make_url(raw_url).set(drivername="postgresql+psycopg")


def _psycopg_url(database_url: URL | str) -> str:
    parsed = database_url if isinstance(database_url, URL) else make_url(database_url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture
def legacy_database() -> Iterator[str]:
    base_url = _postgres_url()
    database_name = f"baraq_ai_legacy_{uuid.uuid4().hex}"
    admin_url = base_url.set(database="postgres")

    try:
        with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    except psycopg.OperationalError as exc:
        pytest.fail(f"configured PostgreSQL is unavailable for migration test: {exc}")

    test_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    try:
        yield test_url
    finally:
        with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


def _run_alembic(database_url: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_SYNC_URL"] = database_url
    subprocess.run(["alembic", *arguments], check=True, env=environment)


def _create_legacy_0004_schema(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            "CREATE TYPE ai_task_type AS ENUM "
            "('FAHES_GENERATE_QUIZ', 'KHOTA_GENERATE_PLAN', "
            "'RASHEED_RECOMMENDATIONS', 'KHOLASA_GENERATE_SUMMARY', "
            "'SADA_TRANSCRIBE_AUDIO')"
        )
        conn.execute(
            "CREATE TYPE ai_provider_account AS ENUM "
            "('PRIMARY', 'SECONDARY', 'GEMINI_PRIMARY', 'MOCK', 'REPLAY')"
        )
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        conn.execute("INSERT INTO alembic_version VALUES (%s)", (LEGACY_REVISION,))
        conn.execute(
            "CREATE TABLE ai_provider_usage_months ("
            "id UUID PRIMARY KEY, marker TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE ai_outputs ("
            "id UUID PRIMARY KEY, task_type ai_task_type NOT NULL, "
            "provider_account ai_provider_account NOT NULL, marker TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE ai_jobs ("
            "id UUID PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, marker TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE ai_source_documents ("
            "id UUID PRIMARY KEY, user_id VARCHAR(64) NOT NULL, "
            "backend_source_id VARCHAR(64) NOT NULL, content_sha256 VARCHAR(64) NOT NULL, "
            "marker TEXT NOT NULL, "
            "CONSTRAINT uq_source_version UNIQUE (backend_source_id, content_sha256))"
        )
        conn.execute(
            "CREATE TABLE ai_job_dispatch_outbox ("
            "id UUID PRIMARY KEY, marker TEXT NOT NULL)"
        )
        fixture_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO ai_provider_usage_months VALUES (%s, 'preserve')", (fixture_id,)
        )
        conn.execute(
            "INSERT INTO ai_outputs VALUES "
            "(%s, 'FAHES_GENERATE_QUIZ', 'PRIMARY', 'preserve')",
            (fixture_id,),
        )
        conn.execute(
            "INSERT INTO ai_jobs VALUES (%s, now(), 'preserve')", (fixture_id,)
        )
        conn.execute(
            "INSERT INTO ai_source_documents VALUES "
            "(%s, 'user-1', 'source-1', repeat('a', 64), 'preserve')",
            (fixture_id,),
        )
        conn.execute(
            "INSERT INTO ai_job_dispatch_outbox VALUES (%s, 'preserve')", (fixture_id,)
        )


def test_legacy_0004_database_upgrades_to_head_without_recreating_enums(
    legacy_database: str,
) -> None:
    _create_legacy_0004_schema(legacy_database)

    _run_alembic(legacy_database, "upgrade", "head")

    with psycopg.connect(_psycopg_url(legacy_database)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        assert conn.execute("SELECT to_regclass('ai_cached_results')").fetchone() == (
            "ai_cached_results",
        )
        assert conn.execute(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='ai_task_type' ORDER BY e.enumsortorder"
        ).fetchall() == [
            ("FAHES_GENERATE_QUIZ",),
            ("KHOTA_GENERATE_PLAN",),
            ("RASHEED_RECOMMENDATIONS",),
            ("KHOLASA_GENERATE_SUMMARY",),
            ("SADA_TRANSCRIBE_AUDIO",),
        ]
        assert conn.execute(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
            "WHERE t.typname='ai_provider_account' ORDER BY e.enumsortorder"
        ).fetchall() == [
            ("PRIMARY",),
            ("SECONDARY",),
            ("GEMINI_PRIMARY",),
            ("MOCK",),
            ("REPLAY",),
        ]
        assert {
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename='ai_cached_results'"
            ).fetchall()
        } >= {
            "ix_ai_cached_results_fingerprint",
            "ix_ai_cached_results_user_id",
            "ix_cached_result_scope",
        }
        for table in (
            "ai_provider_usage_months",
            "ai_outputs",
            "ai_jobs",
            "ai_source_documents",
            "ai_job_dispatch_outbox",
        ):
            assert conn.execute(
                sql.SQL("SELECT marker FROM {} ").format(sql.Identifier(table))
            ).fetchone() == ("preserve",)

    _run_alembic(legacy_database, "downgrade", "0009_result_cache_project_scope")
    _run_alembic(legacy_database, "upgrade", "head")

    with psycopg.connect(_psycopg_url(legacy_database)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        assert conn.execute(
            "SELECT target_queue FROM ai_job_dispatch_outbox"
        ).fetchone() == ("ai_interactive",)
