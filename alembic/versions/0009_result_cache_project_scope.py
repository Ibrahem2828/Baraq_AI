"""Add explicit project scope to reusable result cache entries.

Revision ID: 0009_result_cache_project_scope
Revises: 0008_source_scope_identity
"""

from alembic import op

revision = "0009_result_cache_project_scope"
down_revision = "0008_source_scope_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing entries stay NULL and can never match the new scoped lookup.
    # They age out normally; guessing their historical project would be unsafe.
    op.execute(
        "ALTER TABLE ai_cached_results "
        "ADD COLUMN IF NOT EXISTS project_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cached_result_scope "
        "ON ai_cached_results (user_id, project_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cached_result_scope")
    op.execute("ALTER TABLE ai_cached_results DROP COLUMN IF EXISTS project_id")
