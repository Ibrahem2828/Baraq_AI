from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
async def ready(session: DbSession, redis: RedisClient) -> APIEnvelope[HealthStatus]:
    settings = get_settings()
    database_status = "ok"
    redis_status = "ok"
    status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database_status = "error"
        status = "degraded"
    try:
        await redis.ping()
    except Exception:  # noqa: BLE001
        redis_status = "error"
        status = "degraded"
    return APIEnvelope(
        data=HealthStatus(
            service=settings.app_name,
            version=settings.app_version,
            status=status,
            database=database_status,
            redis=redis_status,
            timestamp=datetime.now(UTC),
        )
    )
