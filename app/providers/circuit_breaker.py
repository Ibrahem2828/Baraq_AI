from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.config import get_settings
from app.models.enums import ProviderAccount


class ProviderCircuitBreaker:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.settings = get_settings()

    def _failure_key(self, account: ProviderAccount) -> str:
        return f"ai:circuit:{account}:failures"

    def _open_key(self, account: ProviderAccount) -> str:
        return f"ai:circuit:{account}:open_until"

    async def is_open(self, account: ProviderAccount) -> bool:
        value = await self.redis.get(self._open_key(account))
        if not value:
            return False
        return float(value) > time.time()

    async def record_success(self, account: ProviderAccount) -> None:
        await self.redis.delete(self._failure_key(account), self._open_key(account))

    async def record_failure(self, account: ProviderAccount) -> None:
        failures = await self.redis.incr(self._failure_key(account))
        await self.redis.expire(self._failure_key(account), self.settings.provider_cooldown_seconds * 2)
        if failures >= self.settings.provider_circuit_failure_threshold:
            open_until = time.time() + self.settings.provider_cooldown_seconds
            await self.redis.set(
                self._open_key(account), open_until, ex=self.settings.provider_cooldown_seconds
            )
