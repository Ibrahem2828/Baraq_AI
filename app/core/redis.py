from __future__ import annotations

from functools import lru_cache
from typing import cast

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return cast(Redis, Redis.from_url(get_settings().redis_url, decode_responses=True))
