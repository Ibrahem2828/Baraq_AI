"""Add reserved_cost_usd for atomic provider budget reservation.

Revision ID: 0005_provider_budget_reservation
Revises: 0004_final_runtime_delivery

Deliberately additive, matching 0004's convention: safe against both an
existing database and a fresh one built from current ORM metadata.
"""

from __future__ import annotations

from alembic import op

revision = "0005_provider_budget_reservation"
down_revision = "0004_final_runtime_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_provider_usage_months
            ADD COLUMN IF NOT EXISTS reserved_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_provider_usage_months DROP COLUMN IF EXISTS reserved_cost_usd")
