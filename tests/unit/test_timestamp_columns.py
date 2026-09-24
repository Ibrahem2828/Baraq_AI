"""Every timestamp column must be timezone-aware.

The service writes `datetime.now(UTC)` everywhere. A column declared as a
bare `Mapped[datetime]` becomes TIMESTAMP WITHOUT TIME ZONE, and asyncpg
refuses to bind an aware value to it -- which is how ai_jobs.started_at made
every job fail on its first status update in production. SQLite does not
enforce the distinction, so this checks the declared metadata instead.
"""

from sqlalchemy import DateTime

from app.db.base import Base
from app.models import load_all_models


def test_every_datetime_column_is_timezone_aware() -> None:
    load_all_models()
    assert "ai_jobs" in Base.metadata.tables
    naive = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert naive == [], f"timestamp columns without time zone: {naive}"
