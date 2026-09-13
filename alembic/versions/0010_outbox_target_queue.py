"""Add target_queue to the job-dispatch outbox for per-task-type Celery routing.

Revision ID: 0010_outbox_target_queue
Revises: 0009_result_cache_project_scope
"""

from alembic import op

revision = "0010_outbox_target_queue"
down_revision = "0009_result_cache_project_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL with an inline DEFAULT backfills every existing row atomically
    # (Postgres 11+: no full table rewrite for a non-volatile default) --
    # existing "process_ai_job" events default to the queue every task type
    # used before this column existed (ai_interactive); Sada jobs recovered
    # after this migration route correctly going forward via
    # app.models.enums.TASK_QUEUE.
    op.execute(
        "ALTER TABLE ai_job_dispatch_outbox "
        "ADD COLUMN IF NOT EXISTS target_queue VARCHAR(32) NOT NULL DEFAULT 'ai_interactive'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_job_dispatch_outbox DROP COLUMN IF EXISTS target_queue")
