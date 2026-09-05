"""Persist trace context and durable AI result delivery state.

Revision ID: 0004_final_runtime_delivery
Revises: 0003_phase1_production_core

This is deliberately additive: 0001 uses metadata at runtime in historical
installations, so each statement is safe both for existing databases and a
fresh database built from the current ORM metadata.
"""

from __future__ import annotations

from alembic import op

revision = "0004_final_runtime_delivery"
down_revision = "0003_phase1_production_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_jobs
            ADD COLUMN IF NOT EXISTS request_id VARCHAR(36),
            ADD COLUMN IF NOT EXISTS source_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS prompt_checksum VARCHAR(64),
            ADD COLUMN IF NOT EXISTS pipeline_version VARCHAR(32),
            ADD COLUMN IF NOT EXISTS allow_fallback BOOLEAN NOT NULL DEFAULT true
        """
    )
    # Existing jobs predate the V2 trace contract.  A stable job UUID is only
    # a migration fallback; all new requests persist their supplied request_id.
    op.execute("UPDATE ai_jobs SET request_id = id::text WHERE request_id IS NULL")
    op.execute("ALTER TABLE ai_jobs ALTER COLUMN request_id SET NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_jobs_request_id ON ai_jobs (request_id)")

    op.execute("ALTER TABLE ai_outputs ADD COLUMN IF NOT EXISTS request_id VARCHAR(36)")
    op.execute(
        """
        UPDATE ai_outputs output
        SET request_id = job.request_id
        FROM ai_jobs job
        WHERE output.job_id = job.id AND output.request_id IS NULL
        """
    )
    op.execute("ALTER TABLE ai_outputs ALTER COLUMN request_id SET NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_outputs_request_id ON ai_outputs (request_id)")

    op.execute("ALTER TABLE ai_job_dispatch_outbox ADD COLUMN IF NOT EXISTS request_id VARCHAR(36)")
    op.execute(
        """
        UPDATE ai_job_dispatch_outbox event
        SET request_id = job.request_id
        FROM ai_jobs job
        WHERE event.job_id = job.id AND event.request_id IS NULL
        """
    )
    op.execute("ALTER TABLE ai_job_dispatch_outbox ALTER COLUMN request_id SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_job_dispatch_outbox_request_id "
        "ON ai_job_dispatch_outbox (request_id)"
    )

    op.execute("ALTER TABLE ai_provider_attempts ADD COLUMN IF NOT EXISTS request_id VARCHAR(36)")
    op.execute(
        """
        UPDATE ai_provider_attempts attempt
        SET request_id = job.request_id
        FROM ai_jobs job
        WHERE attempt.job_id = job.id AND attempt.request_id IS NULL
        """
    )
    op.execute("ALTER TABLE ai_provider_attempts ALTER COLUMN request_id SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ai_provider_attempts_request_id "
        "ON ai_provider_attempts (request_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ai_provider_attempts_request_id")
    op.execute("ALTER TABLE ai_provider_attempts DROP COLUMN IF EXISTS request_id")
    op.execute("DROP INDEX IF EXISTS ix_ai_job_dispatch_outbox_request_id")
    op.execute("ALTER TABLE ai_job_dispatch_outbox DROP COLUMN IF EXISTS request_id")
    op.execute("DROP INDEX IF EXISTS ix_ai_outputs_request_id")
    op.execute("ALTER TABLE ai_outputs DROP COLUMN IF EXISTS request_id")
    op.execute("DROP INDEX IF EXISTS ix_ai_jobs_request_id")
    op.execute(
        """
        ALTER TABLE ai_jobs
            DROP COLUMN IF EXISTS allow_fallback,
            DROP COLUMN IF EXISTS pipeline_version,
            DROP COLUMN IF EXISTS prompt_checksum,
            DROP COLUMN IF EXISTS source_versions,
            DROP COLUMN IF EXISTS request_id
        """
    )
