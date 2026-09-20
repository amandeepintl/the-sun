"""Guild settings against real PostgreSQL and real Redis.

The cache tests are deliberately strict: they check that a value really is served
from Redis (by changing the database behind the service's back and observing that
the cached value still wins) and that invalidation makes the fresh value reappear.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from the_sun.cache import CacheBackend, create_cache_backend
from the_sun.config import CacheConfig
from the_sun.errors import ConfigurationError, InvalidInputError
from the_sun.services import ServiceContext, SettingsService


async def test_first_use_provisions_defaults_and_caches_them(
    service_context: ServiceContext, cache: CacheBackend, unique_id: int
) -> None:
    service = SettingsService(service_context)

    view = await service.get(unique_id)
    assert view.guild_id == unique_id
    assert view.history_length == 20
    assert view.memory_enabled is True
    assert view.ai_provider is None
    assert view.admin_role_ids == []

    raw = await cache.get(service_context.keys.settings(unique_id))
    assert raw is not None, "the settings must have been written to Redis"
    payload = json.loads(raw.decode("utf-8"))
    assert payload["guild_id"] == unique_id
    assert payload["history_length"] == 20


async def test_repeated_reads_come_from_the_cache(
    service_context: ServiceContext, cache: CacheBackend, unique_id: int
) -> None:
    service = SettingsService(service_context)
    await service.get(unique_id)

    # Change the stored row without going through the service.
    async with service_context.unit_of_work() as uow:
        await uow.guild_settings.update(unique_id, {"history_length": 42})

    still_cached = await service.get(unique_id)
    assert still_cached.history_length == 20, "the cached value should have won"

    await service.invalidate(unique_id)
    refreshed = await service.get(unique_id)
    assert refreshed.history_length == 42
    assert await cache.get(service_context.keys.settings(unique_id)) is not None


async def test_updates_persist_and_invalidate(
    service_context: ServiceContext, cache: CacheBackend, unique_id: int
) -> None:
    service = SettingsService(service_context)
    await service.get(unique_id)

    updated = await service.update(
        unique_id,
        {
            "ai_provider": "primary",
            "history_length": 7,
            "memory_enabled": False,
            "ephemeral_responses": False,
            "allowed_channel_ids": [unique_id + 1, unique_id + 2],
            "daily_token_quota": 50_000,
        },
    )
    assert updated.history_length == 7
    assert updated.memory_enabled is False
    assert await cache.get(service_context.keys.settings(unique_id)) is None

    async with service_context.unit_of_work() as uow:
        stored = await uow.guild_settings.get(unique_id)
    assert stored is not None
    assert stored.history_length == 7
    assert stored.allowed_channel_ids == [unique_id + 1, unique_id + 2]

    assert (await service.get(unique_id)).history_length == 7


async def test_channel_allow_list_semantics(
    service_context: ServiceContext, unique_id: int
) -> None:
    service = SettingsService(service_context)
    open_view = await service.get(unique_id)
    assert open_view.is_channel_allowed(unique_id + 300) is True

    restricted = await service.update(unique_id, {"allowed_channel_ids": [unique_id + 1]})
    assert restricted.is_channel_allowed(unique_id + 1) is True
    assert restricted.is_channel_allowed(unique_id + 2) is False
    assert restricted.is_channel_allowed(None) is False


async def test_provider_resolution_falls_back_to_the_default(
    service_context: ServiceContext, unique_id: int
) -> None:
    service = SettingsService(service_context)
    default_view = await service.get(unique_id)
    assert service.provider_for(default_view) == service_context.default_provider_name

    chosen = await service.update(unique_id, {"ai_provider": "primary"})
    assert service.provider_for(chosen) == "primary"


async def test_unknown_setting_is_rejected(service_context: ServiceContext, unique_id: int) -> None:
    service = SettingsService(service_context)
    with pytest.raises(InvalidInputError):
        await service.update(unique_id, {"totally_unknown_setting": 1})


async def test_invalid_guild_id_is_rejected(service_context: ServiceContext) -> None:
    service = SettingsService(service_context)
    with pytest.raises(ConfigurationError):
        await service.get(0)


async def test_settings_survive_a_real_cache_outage(
    service_context: ServiceContext,
    cache: CacheBackend,
    test_cache_config: CacheConfig,
    unique_id: int,
) -> None:
    """A genuinely unreachable Redis must not break configuration reads."""
    unreachable = create_cache_backend(
        replace(test_cache_config, url="redis://127.0.0.1:1/0", safe_url="redis://127.0.0.1:1/0")
    )
    degraded = ServiceContext(
        service_context.settings,
        database=service_context.database,
        cache=unreachable,
        uow_factory=service_context.unit_of_work,
    )
    try:
        view = await SettingsService(degraded).get(unique_id)
        written = await SettingsService(degraded).update(unique_id, {"history_length": 3})
    finally:
        await degraded.providers.aclose()
        await unreachable.close()
    assert view.history_length == 20
    assert written.history_length == 3
    assert await cache.get(service_context.keys.settings(unique_id)) is None
