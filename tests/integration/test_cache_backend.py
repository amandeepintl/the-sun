"""The Redis backend against a real Redis server.

This is the only place the cache layer meets the network, and it covers the
operations the commands depend on: expiring writes, conditional writes, counters,
pattern deletion and the atomic Lua limiter.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from the_sun.cache import CacheBackend, CacheKeys, CacheNamespace, create_cache_backend
from the_sun.config import CacheConfig
from the_sun.errors import CacheUnavailableError

KEYS = CacheKeys(prefix="thesun-test")


async def test_ping_reports_the_real_server_version(cache: CacheBackend) -> None:
    health = await cache.ping()
    assert health.ok is True
    assert "Redis" in health.detail
    assert health.latency_ms >= 0


async def test_value_round_trip_with_expiry(cache: CacheBackend) -> None:
    await cache.set("thesun-test:round-trip", b"payload", ttl_seconds=60)
    assert await cache.get("thesun-test:round-trip") == b"payload"
    remaining = await cache.ttl("thesun-test:round-trip")
    assert remaining is not None and 0 < remaining <= 60
    assert await cache.delete("thesun-test:round-trip") == 1
    assert await cache.get("thesun-test:round-trip") is None
    assert await cache.ttl("thesun-test:round-trip") is None


async def test_missing_keys_read_as_none(cache: CacheBackend) -> None:
    assert await cache.get("thesun-test:never-written") is None
    assert await cache.delete() == 0


async def test_set_if_absent_only_writes_once(cache: CacheBackend) -> None:
    assert await cache.set_if_absent("thesun-test:lock", b"owner-1", ttl_seconds=30) is True
    assert await cache.set_if_absent("thesun-test:lock", b"owner-2", ttl_seconds=30) is False
    assert await cache.get("thesun-test:lock") == b"owner-1"


async def test_counters_accumulate_and_expire(cache: CacheBackend) -> None:
    key = "thesun-test:counter"
    assert await cache.increment(key, ttl_seconds=30) == 1
    assert await cache.increment(key, amount=4) == 5
    assert await cache.get(key) == b"5"
    assert await cache.expire(key, 5) is True
    remaining = await cache.ttl(key)
    assert remaining is not None and remaining <= 5


async def test_deleting_by_pattern_stays_inside_the_namespace(cache: CacheBackend) -> None:
    await cache.set(KEYS.settings(1), b"a")
    await cache.set(KEYS.settings(2), b"b")
    await cache.set(KEYS.conversation_window("other"), b"c")

    removed = await cache.delete_by_pattern(KEYS.namespace_pattern(CacheNamespace.SETTINGS))
    assert removed == 2
    assert await cache.get(KEYS.settings(1)) is None
    assert await cache.get(KEYS.conversation_window("other")) == b"c"


async def test_sliding_window_limiter_allows_then_blocks(cache: CacheBackend) -> None:
    key = KEYS.rate_limit(scope="user", identifier=4242, window_seconds=60)
    now_ms = int(time.time() * 1000)

    for index in range(3):
        result = await cache.run_script(
            "sliding_window_limiter",
            keys=[key],
            args=[now_ms, 60_000, 3, f"event-{index}"],
        )
        assert result[0] == 1
        assert result[1] == index + 1

    blocked = await cache.run_script(
        "sliding_window_limiter", keys=[key], args=[now_ms, 60_000, 3, "event-4"]
    )
    assert blocked[0] == 0
    assert blocked[1] == 3
    assert blocked[2] >= 1, "a blocked caller must be told how long to wait"


async def test_sliding_window_forgets_events_from_outside_the_window(cache: CacheBackend) -> None:
    key = KEYS.rate_limit(scope="user", identifier=99, window_seconds=1)
    now_ms = int(time.time() * 1000)
    first = await cache.run_script(
        "sliding_window_limiter", keys=[key], args=[now_ms, 1_000, 1, "old-event"]
    )
    assert first[0] == 1
    later_ms = now_ms + 5_000
    second = await cache.run_script(
        "sliding_window_limiter", keys=[key], args=[later_ms, 1_000, 1, "new-event"]
    )
    assert second[0] == 1


async def test_unknown_script_is_rejected(cache: CacheBackend) -> None:
    with pytest.raises(KeyError):
        await cache.run_script("does_not_exist", keys=["thesun-test:x"], args=[])


async def test_unreachable_redis_raises_a_typed_error(test_cache_config: CacheConfig) -> None:
    unreachable = create_cache_backend(replace(test_cache_config, url="redis://127.0.0.1:1/0"))
    try:
        health = await unreachable.ping()
        assert health.ok is False
        with pytest.raises(CacheUnavailableError):
            await unreachable.get("thesun-test:anything")
    finally:
        await unreachable.close()
