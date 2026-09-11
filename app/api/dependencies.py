from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.core.security import AuthenticatedDjangoService, verify_and_consume_service_signature
from app.db.session import get_db_session
from app.services.backend_client import BackendClient


async def require_django_service(request: Request) -> AuthenticatedDjangoService:
    """Authenticate an inbound request from the Django gateway, never a client."""
    body = await request.body()
    return await verify_and_consume_service_signature(
        method=request.method,
        target=str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        body=body,
        headers=dict(request.headers),
        redis=get_redis(),
    )


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
IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=8, max_length=128)
]
