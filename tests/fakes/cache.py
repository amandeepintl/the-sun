"""Cache doubles used by unit tests.

``InMemoryCacheBackend`` implements the real protocol so cache-aside behaviour can
be tested without Redis; ``FailingCacheBackend`` simulates an outage so the
degradation paths can be proven. Neither is reachable from application code.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from the_sun.cache import CacheHealth
from the_sun.errors import CacheUnavailableError

__all__ = ["FailingCacheBackend", "InMemoryCacheBackend"]


class InMemoryCacheBackend:
    """A dict-backed cache honouring the CacheBackend contract."""

    def __init__(self, key_prefix: str = "thesun-test") -> None:
        self.key_prefix = key_prefix
        self._values: dict[str, bytes] = {}
        self._expiry: dict[str, float] = {}
        self.calls: list[str] = []

    def _live(self, key: str) -> bool:
        expires_at = self._expiry.get(key)
        if expires_at is None:
            return True
        if expires_at <= time.monotonic():
            self._values.pop(key, None)
            self._expiry.pop(key, None)
            return False
        return True

    async def get(self, key: str) -> bytes | None:
        self.calls.append(f"get:{key}")
        if not self._live(key):
            return None
        return self._values.get(key)

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        self.calls.append(f"set:{key}")
        self._values[key] = value
        if ttl_seconds is not None:
            self._expiry[key] = time.monotonic() + ttl_seconds

    async def set_if_absent(
        self, key: str, value: bytes, *, ttl_seconds: int | None = None
    ) -> bool:
        self.calls.append(f"setnx:{key}")
        if self._live(key) and key in self._values:
            return False
        await self.set(key, value, ttl_seconds=ttl_seconds)
        return True

    async def delete(self, *keys: str) -> int:
        self.calls.append(f"delete:{','.join(keys)}")
        removed = 0
        for key in keys:
            if self._values.pop(key, None) is not None:
                removed += 1
            self._expiry.pop(key, None)
        return removed

    async def increment(self, key: str, amount: int = 1, *, ttl_seconds: int | None = None) -> int:
        self.calls.append(f"incr:{key}")
        current = int(self._values.get(key, b"0"))
        current += amount
        self._values[key] = str(current).encode("utf-8")
        if ttl_seconds is not None:
            self._expiry[key] = time.monotonic() + ttl_seconds
        return current

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        if key not in self._values:
            return False
        self._expiry[key] = time.monotonic() + ttl_seconds
        return True

    async def ttl(self, key: str) -> int | None:
        expires_at = self._expiry.get(key)
        if expires_at is None:
            return None
        return max(0, int(expires_at - time.monotonic()))

    async def run_script(self, name: str, *, keys: Sequence[str], args: Sequence[str | int]) -> Any:
        raise CacheUnavailableError("script execution is not supported by the in-memory double")

    async def delete_by_pattern(self, pattern: str) -> int:
        prefix = pattern.rstrip("*")
        matched = [key for key in self._values if key.startswith(prefix)]
        return await self.delete(*matched)

    async def ping(self) -> CacheHealth:
        return CacheHealth(ok=True, detail="in-memory double", latency_ms=0.0)

    async def close(self) -> None:
        self._values.clear()


class FailingCacheBackend(InMemoryCacheBackend):
    """A cache that is always unavailable, for testing degradation."""

    async def get(self, key: str) -> bytes | None:
        raise CacheUnavailableError("simulated cache outage")

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None:
        raise CacheUnavailableError("simulated cache outage")

    async def delete(self, *keys: str) -> int:
        raise CacheUnavailableError("simulated cache outage")
