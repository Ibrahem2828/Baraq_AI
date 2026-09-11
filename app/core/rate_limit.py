from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.errors import RateLimitError


async def enforce_rate_limit(
    *, redis: Redis, key: str, limit_per_minute: int, window_seconds: int = 60
) -> None:
    """Fixed-window per-key rate limit backed by Redis (AI_REQUESTS_PER_MINUTE).

    A single INCR keeps the common case to one Redis round trip; the window
    key rotates every ``window_seconds`` so no separate reset job is needed.
    ``limit_per_minute <= 0`` disables the limit (used by tests/lab).
    """
    if limit_per_minute <= 0:
        return
    window = int(time.time()) // window_seconds
    redis_key = f"ai:ratelimit:{key}:{window}"
    current = await redis.incr(redis_key)
    if current == 1:
        await redis.expire(redis_key, window_seconds)
    if current > limit_per_minute:
        raise RateLimitError(
            f"Rate limit exceeded: max {limit_per_minute} AI job requests per minute",
            details={"limit_per_minute": limit_per_minute, "window_seconds": window_seconds},
        )
