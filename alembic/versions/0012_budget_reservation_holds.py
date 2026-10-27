"""Add ai_budget_reservation_holds: a durable per-job reservation ledger.

Revision ID: 0012_budget_reservation_holds
Revises: 0011_ai_job_timestamps_timezone

Closes a real production-readiness gap: ai_provider_usage_months.reserved_cost_usd
(0005_provider_budget_reservation) is only a running total with no record of
which job holds how much. A worker killed mid-call (celery_task_time_limit_seconds,
OOM, deploy restart) left that amount permanently stuck as "reserved" with no
code anywhere able to identify or release it. This table makes each
reservation its own row, keyed to the job, so job_recovery can find and
release whatever a dead worker left behind before marking the job FAILED.

Deliberately additive, matching prior migrations' convention: safe against
both an existing database and a fresh one built from current ORM metadata.
"""

from __future__ import annotations

from alembic import op

revision = "0012_budget_reservation_holds"
down_revision = "0011_ai_job_timestamps_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'ai_budget_reservation_hold_account'
            ) THEN
                CREATE TYPE ai_budget_reservation_hold_account AS ENUM (
                    'PRIMARY', 'SECONDARY', 'GEMINI_PRIMARY', 'MOCK', 'REPLAY'
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_budget_reservation_holds (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
            provider_account ai_budget_reservation_hold_account NOT NULL,
            reserved_usd DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_budget_reservation_hold_job
            ON ai_budget_reservation_holds (job_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_budget_reservation_holds")
    op.execute("DROP TYPE IF EXISTS ai_budget_reservation_hold_account")
