"""Add ai_cached_results (Phase B3 result cache) and AIOutput.result_cache_hit.

Revision ID: 0006_result_cache
Revises: 0005_provider_budget_reservation

Deliberately additive, matching prior migrations' convention: safe against
both an existing database and a fresh one built from current ORM metadata.
The ai_task_type/ai_provider_account enum types already exist (created by
0001 for ai_jobs/ai_outputs) -- create_type=False avoids a duplicate-type
error when referencing them from this new table.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_result_cache"
down_revision = "0005_provider_budget_reservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_outputs ADD COLUMN IF NOT EXISTS result_cache_hit "
        "BOOLEAN NOT NULL DEFAULT false"
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ai_cached_results" in inspector.get_table_names():
        return

    op.create_table(
        "ai_cached_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column(
            "task_type",
            sa.Enum(name="ai_task_type", create_type=False),
            nullable=False,
        ),
        sa.Column("result_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("groundedness_score", sa.Float(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("security_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "validation_status", sa.String(length=32), nullable=False, server_default="valid"
        ),
        sa.Column("source_model_name", sa.String(length=100), nullable=False),
        sa.Column(
            "source_provider_account",
            sa.Enum(name="ai_provider_account", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "source_estimated_cost_usd", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_cached_results_fingerprint",
        "ai_cached_results",
        ["fingerprint"],
        unique=True,
    )
    op.create_index("ix_ai_cached_results_user_id", "ai_cached_results", ["user_id"])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_cached_results")
    op.execute("ALTER TABLE ai_outputs DROP COLUMN IF EXISTS result_cache_hit")
