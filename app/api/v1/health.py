from __future__ import annotations

from datetime import UTC, datetime

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from app.api.dependencies import DbSession, RedisClient
from app.core.config import get_settings
from app.schemas.common import APIEnvelope, HealthStatus

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", response_model=APIEnvelope[HealthStatus])
async def live() -> APIEnvelope[HealthStatus]:
    settings = get_settings()
    return APIEnvelope(
        data=HealthStatus(
            service=settings.app_name,
            version=settings.app_version,
            status="ok",
            timestamp=datetime.now(UTC),
        )
    )


@router.get("/ready", response_model=APIEnvelope[HealthStatus])
async def ready(
    session: DbSession, redis: RedisClient
) -> APIEnvelope[HealthStatus] | ORJSONResponse:
    settings = get_settings()
    database_status = "ok"
    redis_status = "ok"
    migrations_status = "ok"
    status = "ok"
    try:
        await session.execute(text("SELECT 1"))
        current_revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        alembic_config = Config("alembic.ini")
        expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
        if current_revision != expected_revision:
            migrations_status = "error"
            status = "degraded"
    except Exception:
        database_status = "error"
        migrations_status = "error"
        status = "degraded"
    try:
        await redis.ping()
    except Exception:
        redis_status = "error"
        status = "degraded"
    payload: APIEnvelope[HealthStatus] = APIEnvelope(
        data=HealthStatus(
            service=settings.app_name,
            version=settings.app_version,
            status=status,
            database=database_status,
            redis=redis_status,
            migrations=migrations_status,
            timestamp=datetime.now(UTC),
        )
    )
    return ORJSONResponse(
        status_code=200 if status == "ok" else 503,
        content=payload.model_dump(mode="json"),
    )
