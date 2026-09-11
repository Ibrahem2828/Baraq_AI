"""Add Phase 1 durable jobs, security metadata and state support.

Revision ID: 0003_phase1_production_core
Revises: 0002_vector_indexes
"""

from __future__ import annotations

from alembic import op

revision = "0003_phase1_production_core"
down_revision = "0002_vector_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `0001_initial` historically derives schema from ORM metadata.  These
    # idempotent statements make this additive revision safe for both existing
    # databases and an empty database upgraded from the current source tree.
    for status in ("preparing", "planning", "repairing"):
        op.execute(f"ALTER TYPE ai_job_status ADD VALUE IF NOT EXISTS '{status}'")
    op.execute(
        """
        ALTER TABLE ai_outputs
        ADD COLUMN IF NOT EXISTS warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS security_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN IF NOT EXISTS validation_report JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE ai_dispatch_outbox_status AS ENUM ('pending', 'dispatching', 'dispatched');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_job_dispatch_outbox (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES ai_jobs(id) ON DELETE CASCADE,
            event_type VARCHAR(64) NOT NULL DEFAULT 'process_ai_job',
            status ai_dispatch_outbox_status NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            locked_at TIMESTAMP WITH TIME ZONE,
            last_error TEXT,
            dispatched_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_ai_job_dispatch_outbox_job_event UNIQUE (job_id, event_type)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_jobs_user_backend_request
        ON ai_jobs (user_id, backend_request_id)
        WHERE backend_request_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ai_job_dispatch_outbox_pending
        ON ai_job_dispatch_outbox (status, available_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_job_dispatch_outbox_pending")
    op.execute("DROP INDEX IF EXISTS uq_ai_jobs_user_backend_request")
    op.execute("DROP TABLE IF EXISTS ai_job_dispatch_outbox")
    op.execute("DROP TYPE IF EXISTS ai_dispatch_outbox_status")
    op.execute(
        """
        ALTER TABLE ai_outputs
        DROP COLUMN IF EXISTS validation_report,
        DROP COLUMN IF EXISTS security_flags,
        DROP COLUMN IF EXISTS warnings
        """
    )
