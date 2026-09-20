"""Cache contract.

Services depend on this protocol rather than on redis-py, which keeps the cache
implementation replaceable and, more importantly, keeps every Redis call inside
this package.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["CacheBackend", "CacheHealth"]


@dataclass(frozen=True, slots=True)
class CacheHealth:
    """Result of a real round trip against the cache."""

    ok: bool
    detail: str
    latency_ms: float

    @property
    def version(self) -> str | None:
        return self.detail if self.ok else None


@runtime_checkable
class CacheBackend(Protocol):
    """The subset of Redis behaviour this project relies on."""

    @property
    def key_prefix(self) -> str:
        """Namespace prefix this backend applies to every key it writes."""
        ...

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> None: ...

    async def set_if_absent(
        self, key: str, value: bytes, *, ttl_seconds: int | None = None
    ) -> bool: ...

    async def delete(self, *keys: str) -> int: ...

    async def increment(
        self, key: str, amount: int = 1, *, ttl_seconds: int | None = None
    ) -> int: ...

    async def expire(self, key: str, ttl_seconds: int) -> bool: ...

    async def ttl(self, key: str) -> int | None: ...

    async def run_script(
        self, name: str, *, keys: Sequence[str], args: Sequence[str | int]
    ) -> Any: ...

    async def delete_by_pattern(self, pattern: str) -> int: ...

    async def ping(self) -> CacheHealth: ...

    async def close(self) -> None: ...
