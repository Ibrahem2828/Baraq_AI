"""Add pgvector ANN indexes.

Revision ID: 0002_vector_indexes
Revises: 0001_initial
"""

from __future__ import annotations

from alembic import op

revision = "0002_vector_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_chunks_embedding_hnsw "
        "ON ai_source_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_source_chunks_embedding_hnsw")
