"""SQLAlchemy models.

Use ``load_all_models`` from Alembic/startup when metadata registration is required.
Avoid eager imports here so pure schema and utility modules remain lightweight.
"""


def load_all_models() -> None:
    from app.models import ai_job, feedback, prompt, source  # noqa: F401


__all__ = ["load_all_models"]
