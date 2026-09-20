"""Cache keys, TTL policy and cache-aside behaviour."""

from __future__ import annotations

import json

from tests.fakes import FailingCacheBackend, InMemoryCacheBackend

from the_sun.cache import (
    DEFAULT_TTLS,
    CacheKeys,
    CacheNamespace,
    get_or_load,
    invalidate,
    read_json,
    write_json,
)

KEYS = CacheKeys(prefix="thesun-test")


def test_every_key_is_namespaced() -> None:
    assert KEYS.settings(123) == "thesun-test:settings:123"
    assert KEYS.rate_limit(scope="user", identifier=9, window_seconds=60).startswith(
        "thesun-test:ratelimit:user:9:"
    )
    assert len({KEYS.settings(1), KEYS.settings(2)}) == 2


def test_namespace_pattern_targets_exactly_one_namespace() -> None:
    pattern = KEYS.namespace_pattern(CacheNamespace.SETTINGS)
    assert pattern == "thesun-test:settings:*"
    assert not pattern.startswith(KEYS.namespace_pattern(CacheNamespace.RATE_LIMIT))


def test_every_namespace_has_a_ttl() -> None:
    assert set(DEFAULT_TTLS) == set(CacheNamespace)
    assert all(ttl > 0 for ttl in DEFAULT_TTLS.values())


async def test_json_round_trip() -> None:
    cache = InMemoryCacheBackend()
    await write_json(cache, "thesun-test:doc", {"value": 1}, ttl_seconds=30)
    assert await read_json(cache, "thesun-test:doc") == {"value": 1}
    remaining = await cache.ttl("thesun-test:doc")
    assert remaining is not None and 0 < remaining <= 30


async def test_unreadable_cache_entries_are_dropped() -> None:
    cache = InMemoryCacheBackend()
    await cache.set("thesun-test:broken", b"{not json")
    assert await read_json(cache, "thesun-test:broken") is None
    assert await cache.get("thesun-test:broken") is None


async def test_get_or_load_reads_through_and_caches() -> None:
    cache = InMemoryCacheBackend()
    loads = 0

    async def loader() -> dict[str, int]:
        nonlocal loads
        loads += 1
        return {"runs": loads}

    first = await get_or_load(
        cache,
        key="thesun-test:through",
        ttl_seconds=60,
        loader=loader,
        dump=lambda value: value,
        load=lambda payload: payload,
    )
    second = await get_or_load(
        cache,
        key="thesun-test:through",
        ttl_seconds=60,
        loader=loader,
        dump=lambda value: value,
        load=lambda payload: payload,
    )
    assert first == {"runs": 1}
    assert second == {"runs": 1}
    assert loads == 1


async def test_get_or_load_reloads_when_the_cached_shape_is_stale() -> None:
    cache = InMemoryCacheBackend()
    await cache.set("thesun-test:stale", json.dumps({"unexpected": True}).encode())

    async def loader() -> list[str]:
        return ["fresh"]

    value = await get_or_load(
        cache,
        key="thesun-test:stale",
        ttl_seconds=60,
        loader=loader,
        dump=lambda item: {"items": item},
        load=lambda payload: payload["items"],
    )
    assert value == ["fresh"]


async def test_cache_outage_degrades_to_the_source_of_truth() -> None:
    cache = FailingCacheBackend()

    async def loader() -> str:
        return "from the database"

    value = await get_or_load(
        cache,
        key="thesun-test:outage",
        ttl_seconds=60,
        loader=loader,
        dump=lambda item: {"value": item},
        load=lambda payload: payload["value"],
    )
    assert value == "from the database"
    assert await invalidate(cache, "thesun-test:outage") == 0
    assert await read_json(cache, "thesun-test:outage") is None
