"""Store ai_jobs.started_at/completed_at as timestamptz.

Revision ID: 0011_ai_job_timestamps_timezone
Revises: 0010_outbox_target_queue
"""

from alembic import op

revision = "0011_ai_job_timestamps_timezone"
down_revision = "0010_outbox_target_queue"
branch_labels = None
depends_on = None

COLUMNS = ("started_at", "completed_at")


def upgrade() -> None:
    # Both columns were declared without a type, so they became TIMESTAMP
    # WITHOUT TIME ZONE while the worker writes aware UTC datetimes. asyncpg
    # refuses that bind, so every job failed on its first status update.
    # Existing values were written as UTC; AT TIME ZONE 'UTC' keeps them.
    for column in COLUMNS:
        _retype(column, "timestamp without time zone", "TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    for column in COLUMNS:
        _retype(column, "timestamp with time zone", "TIMESTAMP WITHOUT TIME ZONE")


def _retype(column: str, current: str, target: str) -> None:
    # Only a column that exists with the expected current type is converted,
    # so the migration is a no-op on schemas that never had it (or already
    # have the target type) instead of failing the upgrade.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'ai_jobs'
                  AND column_name = '{column}' AND data_type = '{current}'
            ) THEN
                ALTER TABLE ai_jobs ALTER COLUMN {column} TYPE {target}
                    USING {column} AT TIME ZONE 'UTC';
            END IF;
        END $$;
        """
    )
