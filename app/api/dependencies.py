from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthenticationError, AuthorizationError, RateLimitError
from app.core.redis import get_redis
from app.core.security import AuthenticatedUser, decode_access_token
from app.db.session import get_db_session
from app.services.backend_client import BackendClient

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Bearer access token is required")
    return decode_access_token(credentials.credentials)



async def get_rate_limited_user(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthenticatedUser:
    settings = get_settings()
    if settings.ai_requests_per_minute <= 0:
        return user
    key = f"baraq-ai:rate:{user.user_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > settings.ai_requests_per_minute:
        ttl = max(1, await redis.ttl(key))
        raise RateLimitError(
            "AI request rate limit exceeded",
            details={"retry_after_seconds": ttl},
        )
    return user


def require_admin(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if user.role not in {"admin", "super_admin"}:
        raise AuthorizationError("Administrator permission is required")
    return user


async def get_backend_client() -> AsyncIterator[BackendClient]:
    client = BackendClient()
    try:
        yield client
    finally:
        await client.aclose()


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
RateLimitedUser = Annotated[AuthenticatedUser, Depends(get_rate_limited_user)]
AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]
Backend = Annotated[BackendClient, Depends(get_backend_client)]
RedisClient = Annotated[Redis, Depends(get_redis)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
