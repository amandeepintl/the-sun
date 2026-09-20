"""Redis-backed cache.

Every Redis call in the project lives behind this class. Failures are translated
into :class:`CacheUnavailableError` so services can decide how to degrade instead
of leaking redis-py exceptions upwards.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from redis import RedisError
from redis.asyncio import Redis

from the_sun.cache.backend import CacheHealth
from the_sun.cache.scripts import SCRIPTS
from the_sun.config import CacheConfig
from the_sun.errors import CacheUnavailableError

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from redis.commands.core import AsyncScript

__all__ = ["RedisCacheBackend", "create_cache_backend"]

logger = logging.getLogger(__name__)


class RedisCacheBackend:
    """Async Redis client implementing :class:`the_sun.cache.backend.CacheBackend`."""

    def __init__(self, config: CacheConfig) -> None:
        self._config = config
        self._client: Redis | None = None
        self._scripts: dict[str, AsyncScript] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @property
    def key_prefix(self) -> str:
        return self._config.key_prefix

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis.from_url(
                self._config.url,
                max_connections=self._config.max_connections,
                decode_responses=False,
                health_check_interval=30,
                socket_keepalive=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._scripts.clear()

    def _script(self, name: str) -> AsyncScript:
        script = self._scripts.get(name)
        if script is None:
            try:
                definition = SCRIPTS[name]
            except KeyError as exc:
                raise KeyError(f"unknown cache script {name!r}") from exc
            script = self.client.register_script(definition.source)
            self._scripts[name] = script
        return script

    # ------------------------------------------------------------------ #
    # Core operations
    # ------------------------------------------------------------------ #
    async def get(self, key: str) -> bytes | None:
        try:
            value = await self.client.get(key)
        except RedisError as exc:
            raise CacheUnavailableError(f"GET {key} failed: {exc}") from exc
        if value is None or isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        try:
            await self.client.set(key, value, ex=ttl_seconds)
        except RedisError as exc:
            raise CacheUnavailableError(f"SET {key} failed: {exc}") from exc

    async def set_if_absent(
        self, key: str, value: bytes, *, ttl_seconds: int | None = None
    ) -> bool:
        try:
            created = await self.client.set(key, value, ex=ttl_seconds, nx=True)
        except RedisError as exc:
            raise CacheUnavailableError(f"SET NX {key} failed: {exc}") from exc
        return bool(created)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        try:
            return int(await self.client.delete(*keys))
        except RedisError as exc:
            raise CacheUnavailableError(f"DEL {keys!r} failed: {exc}") from exc

    async def increment(self, key: str, amount: int = 1, *, ttl_seconds: int | None = None) -> int:
        try:
            pipeline = self.client.pipeline()
            pipeline.incrby(key, amount)
            if ttl_seconds is not None:
                pipeline.expire(key, ttl_seconds, nx=True)
            results = await pipeline.execute()
        except RedisError as exc:
            raise CacheUnavailableError(f"INCRBY {key} failed: {exc}") from exc
        return int(results[0])

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        try:
            return bool(await self.client.expire(key, ttl_seconds))
        except RedisError as exc:
            raise CacheUnavailableError(f"EXPIRE {key} failed: {exc}") from exc

    async def ttl(self, key: str) -> int | None:
        try:
            value = await self.client.ttl(key)
        except RedisError as exc:
            raise CacheUnavailableError(f"TTL {key} failed: {exc}") from exc
        if value is None or int(value) < 0:
            return None
        return int(value)

    async def run_script(self, name: str, *, keys: Sequence[str], args: Sequence[str | int]) -> Any:
        script = self._script(name)
        try:
            return await script(keys=list(keys), args=list(args))
        except RedisError as exc:
            raise CacheUnavailableError(f"EVAL {name} failed: {exc}") from exc

    async def delete_by_pattern(self, pattern: str) -> int:
        removed = 0
        try:
            async for key in self.client.scan_iter(match=pattern, count=200):
                removed += int(await self.client.delete(key))
        except RedisError as exc:
            raise CacheUnavailableError(f"SCAN {pattern} failed: {exc}") from exc
        return removed

    async def ping(self) -> CacheHealth:
        started = time.perf_counter()
        try:
            await self.client.ping()
            info = await self.client.info(section="server")
        except RedisError as exc:
            return CacheHealth(
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        latency = (time.perf_counter() - started) * 1000
        version = info.get("redis_version") if isinstance(info, dict) else None
        return CacheHealth(
            ok=True, detail=f"Redis {version or 'unknown version'}", latency_ms=latency
        )


def create_cache_backend(config: CacheConfig) -> RedisCacheBackend:
    """Build the Redis-backed cache from resolved settings."""
    return RedisCacheBackend(config)
