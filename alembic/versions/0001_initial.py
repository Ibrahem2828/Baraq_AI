"""Initial Baraq AI service schema.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    from app.db.base import Base
    from app.models import load_all_models

    load_all_models()
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from app.db.base import Base
    from app.models import load_all_models

    load_all_models()
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
