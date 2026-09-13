"""Scope source-document identity by user, project, source and content version.

Revision ID: 0008_source_scope_identity
Revises: 0007_project_scoping
"""

from alembic import op

revision = "0008_source_scope_identity"
down_revision = "0007_project_scoping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_source_documents DROP CONSTRAINT IF EXISTS uq_source_version")
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE ai_source_documents
                ADD CONSTRAINT uq_source_scope_version
                UNIQUE (user_id, project_id, backend_source_id, content_sha256);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_source_documents DROP CONSTRAINT IF EXISTS uq_source_scope_version")
    op.execute(
        """
        DO $$ BEGIN
            ALTER TABLE ai_source_documents
                ADD CONSTRAINT uq_source_version
                UNIQUE (backend_source_id, content_sha256);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
