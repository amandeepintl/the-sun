"""Cache-aside helpers.

Services describe how to load a value and how to serialise it; this module owns
the read-through logic, including the decision to fall back to the loader when
the cache is unavailable rather than failing the user's request.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from the_sun.cache.backend import CacheBackend
from the_sun.errors import CacheUnavailableError

__all__ = ["get_or_load", "invalidate", "read_json", "write_json"]

logger = logging.getLogger(__name__)


async def read_json(cache: CacheBackend, key: str) -> Any | None:
    """Read a JSON document, returning ``None`` on miss or unusable content."""
    try:
        raw = await cache.get(key)
    except CacheUnavailableError as exc:
        logger.warning(
            "cache read failed, falling back to source", extra={"key": key, "error": str(exc)}
        )
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("dropping unreadable cache entry", extra={"key": key})
        with contextlib.suppress(CacheUnavailableError):
            await cache.delete(key)
        return None


async def write_json(
    cache: CacheBackend, key: str, payload: Any, *, ttl_seconds: int | None = None
) -> None:
    """Write a JSON document, tolerating an unavailable cache."""
    encoded = json.dumps(payload, default=str).encode("utf-8")
    try:
        await cache.set(key, encoded, ttl_seconds=ttl_seconds)
    except CacheUnavailableError as exc:
        logger.warning("cache write failed", extra={"key": key, "error": str(exc)})


async def invalidate(cache: CacheBackend, *keys: str) -> int:
    """Delete keys, tolerating an unavailable cache."""
    if not keys:
        return 0
    try:
        return await cache.delete(*keys)
    except CacheUnavailableError as exc:
        logger.warning("cache invalidation failed", extra={"keys": list(keys), "error": str(exc)})
        return 0


async def get_or_load[T](
    cache: CacheBackend,
    *,
    key: str,
    ttl_seconds: int,
    loader: Callable[[], Awaitable[T]],
    dump: Callable[[T], Any],
    load: Callable[[Any], T],
) -> T:
    """Return the cached value, loading and caching it on a miss."""
    cached = await read_json(cache, key)
    if cached is not None:
        try:
            return load(cached)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "cache entry no longer matches the current schema",
                extra={"key": key, "error": str(exc)},
            )
            await invalidate(cache, key)
    value = await loader()
    await write_json(cache, key, dump(value), ttl_seconds=ttl_seconds)
    return value
