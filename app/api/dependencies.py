from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import AuthenticatedDjangoService, verify_service_signature
from app.db.session import get_db_session
from app.services.backend_client import BackendClient


async def require_django_service(request: Request) -> AuthenticatedDjangoService:
    """Authenticate an inbound request from the Django gateway, never a client."""
    body = await request.body()
    verify_service_signature(
        method=request.method,
        path=request.url.path,
        body=body,
        headers=dict(request.headers),
    )
    return AuthenticatedDjangoService(service_id=get_settings().baraq_django_service_id)


async def get_backend_client() -> AsyncIterator[BackendClient]:
    client = BackendClient()
    try:
        yield client
    finally:
        await client.aclose()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
DjangoService = Annotated[AuthenticatedDjangoService, Depends(require_django_service)]
Backend = Annotated[BackendClient, Depends(get_backend_client)]
RedisClient = Annotated[Redis, Depends(get_redis)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
