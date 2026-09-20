"""Usage recording and statistics against real PostgreSQL."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from the_sun.db import create_database
from the_sun.db.models import UsageStatus, UsageSurface
from the_sun.services import ServiceContext, UsageRecord, UsageService


async def test_records_are_aggregated_from_the_database(
    service_context: ServiceContext, unique_id: int
) -> None:
    service = UsageService(service_context)
    guild_id, user_id, channel_id = unique_id, unique_id + 1, unique_id + 2

    await service.record(
        UsageRecord(
            command="ask",
            surface=UsageSurface.SLASH_COMMAND,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            provider="primary",
            model="unit-test-model",
            prompt_tokens=120,
            completion_tokens=80,
            latency_ms=640,
            correlation_id="integration-test",
        )
    )
    await service.record_many(
        [
            UsageRecord(
                command="ask",
                surface=UsageSurface.SLASH_COMMAND,
                guild_id=guild_id,
                user_id=user_id,
                provider="primary",
                model="unit-test-model",
                prompt_tokens=100,
                completion_tokens=50,
                latency_ms=500,
                status=UsageStatus.ERROR,
                error_code="ProviderTimeoutError",
            ),
            UsageRecord(
                command="translate",
                surface=UsageSurface.CONTEXT_MENU,
                guild_id=guild_id,
                user_id=user_id,
                provider="secondary",
                model="other-model",
                prompt_tokens=20,
                completion_tokens=10,
                latency_ms=100,
            ),
        ]
    )

    summary = await service.summary(guild_id=guild_id)
    assert summary.invocations == 3
    assert summary.successful == 2
    assert summary.failed == 1
    assert summary.prompt_tokens == 240
    assert summary.completion_tokens == 140
    assert summary.average_latency_ms is not None
    assert 100 <= summary.average_latency_ms <= 640

    top = await service.top_commands(guild_id=guild_id)
    assert top[0].command == "ask"
    assert top[0].invocations == 2
    assert top[0].failures == 1

    providers = await service.provider_breakdown(guild_id=guild_id)
    assert {entry.provider for entry in providers} == {"primary", "secondary"}

    daily = await service.daily_series(guild_id=guild_id)
    assert daily[0].invocations == 3

    user_summary = await service.summary(user_id=user_id)
    assert user_summary.invocations == 3

    other_guild = await service.summary(guild_id=guild_id + 500)
    assert other_guild.invocations == 0
    assert other_guild.average_latency_ms is None


async def test_windows_are_measured_from_the_service_clock(service_context: ServiceContext) -> None:
    service = UsageService(service_context)
    assert service.window_start() is None
    start = service.window_start(hours=24)
    assert start is not None
    assert start < service_context.clock()


async def test_events_can_be_paginated(service_context: ServiceContext, unique_id: int) -> None:
    service = UsageService(service_context)
    for index in range(3):
        await service.record(
            UsageRecord(command="ask", guild_id=unique_id, user_id=unique_id + 1, latency_ms=index)
        )
    first = await service.events_page(guild_id=unique_id, limit=2)
    assert len(first.items) == 2
    second = await service.events_page(guild_id=unique_id, limit=2, cursor=first.next_cursor)
    assert len(second.items) == 1


async def test_recording_failures_never_break_the_caller(
    service_context: ServiceContext, unique_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Telemetry must not take a command down with it, even when the database is gone."""
    broken_database = create_database(
        replace(
            service_context.database.config, url="postgresql+asyncpg://user:pw@127.0.0.1:1/none"
        )
    )
    degraded = ServiceContext(
        service_context.settings, database=broken_database, cache=service_context.cache
    )
    try:
        with caplog.at_level(logging.WARNING, logger="the_sun.services.usage_service"):
            await UsageService(degraded).record(
                UsageRecord(command="ask", guild_id=unique_id, user_id=unique_id)
            )
    finally:
        await broken_database.dispose()
        await degraded.providers.aclose()
    assert any("could not be recorded" in record.message for record in caplog.records)
