"""Add project_id to ai_jobs and ai_source_documents.

Revision ID: 0007_project_scoping
Revises: 0006_result_cache

Baraq_MD_Blueprint 01_BACKEND.md §5.2 / 02_AI_PLATFORM.md §3.2: retrieval
and job records must be scoped by project, not just user_id -- user_id
alone is not a sufficient authorization boundary. Nullable (no backfill)
because pre-existing rows predate project scoping on the Django side and
self-heal on next access (ensure_ingested) or simply age out (ai_jobs).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_project_scoping"
down_revision = "0006_result_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_jobs ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_jobs_project_created "
        "ON ai_jobs (project_id, created_at)"
    )
    op.execute(
        "ALTER TABLE ai_source_documents ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_project ON ai_source_documents (project_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_source_project")
    op.execute("ALTER TABLE ai_source_documents DROP COLUMN IF EXISTS project_id")
    op.execute("DROP INDEX IF EXISTS ix_ai_jobs_project_created")
    op.execute("ALTER TABLE ai_jobs DROP COLUMN IF EXISTS project_id")
