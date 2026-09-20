"""Live checks against the real provider, database, cache and Discord.

These are opt-in: set ``SUN_LIVE_TESTS=1`` and run ``pytest -m live``. They make
real requests and cost real (tiny) amounts of quota, which is why they never run
as part of the default suite. Nothing here is stubbed.
"""

from __future__ import annotations

import os

import pytest

from the_sun.ai import ChatMessage
from the_sun.services import HealthService, ServiceContext, UsageService
from the_sun.services.usage_service import UsageRecord

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SUN_LIVE_TESTS") != "1",
        reason="set SUN_LIVE_TESTS=1 to run tests that call real external services",
    ),
]


async def test_configured_provider_answers_a_real_prompt(service_context: ServiceContext) -> None:
    """A real completion from the configured provider, with real token usage."""
    provider = service_context.providers.resolve()
    result = await provider.chat(
        [ChatMessage.user("Reply with a single short sentence about the sun.")],
        max_tokens=64,
    )
    assert result.content.strip(), "the provider returned no text"
    assert result.provider == provider.name
    assert result.model
    assert result.latency_ms is not None and result.latency_ms > 0
    assert result.usage is not None
    assert (result.usage.total_tokens or 0) > 0


async def test_provider_model_catalogue_is_reachable(service_context: ServiceContext) -> None:
    health = await service_context.providers.get().health_check()
    assert health.ok, health.detail
    assert health.models, "the provider reported no models"


async def test_every_configured_provider_authenticates(service_context: ServiceContext) -> None:
    for health in await service_context.providers.health_all():
        assert health.ok, f"{health.provider}: {health.detail}"


async def test_usage_from_a_real_call_is_persisted(service_context: ServiceContext) -> None:
    """End to end: a real provider call produces a real usage row."""
    provider = service_context.providers.resolve()
    result = await provider.chat([ChatMessage.user("Say the word: verified.")], max_tokens=32)
    assert result.usage is not None

    guild_id = 0
    async with service_context.unit_of_work() as uow:
        row = await uow.guild_settings.get_or_create(
            int(os.environ.get("SUN_LIVE_TEST_GUILD_ID", "1"))
        )
        guild_id = row.guild_id

    usage = UsageService(service_context)
    await usage.record(
        UsageRecord(
            command="live-check",
            guild_id=guild_id,
            provider=result.provider,
            model=result.model,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            latency_ms=int(result.latency_ms or 0),
        )
    )
    summary = await usage.summary(guild_id=guild_id)
    assert summary.invocations >= 1
    assert summary.prompt_tokens is not None


async def test_health_report_covers_every_dependency(service_context: ServiceContext) -> None:
    report = await HealthService(service_context).run()
    names = {check.name for check in report.checks}
    assert "config" in names
    assert "database" in names
    assert "cache" in names
    assert "discord" in names
    assert any(name.startswith("provider:") for name in names)
    assert report.ok, [check.to_dict() for check in report.failures]
