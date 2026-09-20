"""Rate limiting policy.

The Redis script itself is covered by an integration test; here the policy is
tested against a cache double that mimics the script's contract, including the
degradation path when Redis is unavailable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.fakes import FailingCacheBackend, InMemoryCacheBackend

from the_sun.config import Settings
from the_sun.errors import CacheUnavailableError, RateLimitedError
from the_sun.services import GuildSettingsView, RateLimitService, ServiceContext

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class ScriptedCache(InMemoryCacheBackend):
    """Cache double that answers ``run_script`` the way the Lua limiter does."""

    def __init__(self, *, allow: bool = True, retry_after: int = 7) -> None:
        super().__init__()
        self.allow = allow
        self.retry_after = retry_after
        self.script_calls: list[dict[str, Any]] = []

    async def run_script(self, name: str, *, keys: Sequence[str], args: Sequence[str | int]) -> Any:
        self.script_calls.append({"name": name, "keys": list(keys), "args": list(args)})
        limit = int(args[2])
        if self.allow:
            return [1, 1, 0]
        return [0, limit, self.retry_after]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DISCORD_TOKEN": "unit-test-discord-token",
        "DATABASE_URL": "postgresql://unit:unit@localhost:5432/unit",
        "REDIS_URL": "redis://localhost:6379/0",
        "AI_PROVIDERS": json.dumps(
            {
                "primary": {
                    "type": "openai_compatible",
                    "base_url": "https://provider.invalid/v1",
                    "default_model": "unit-test-model",
                }
            }
        ),
        "DEFAULT_PROVIDER": "primary",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _guild_settings(**overrides: object) -> GuildSettingsView:
    """Guild settings with every override left unset unless a test sets one."""
    values: dict[str, object] = {
        "guild_id": 1234,
        "ai_provider": None,
        "ai_model": None,
        "system_prompt": None,
        "history_length": 20,
        "memory_enabled": True,
        "ephemeral_responses": True,
        "rate_limit_per_user_per_minute": None,
        "rate_limit_per_guild_per_minute": None,
        "daily_token_quota": None,
        "admin_role_ids": [],
        "allowed_channel_ids": [],
        "history_retention_days": None,
        "updated_at": NOW,
    }
    values.update(overrides)
    return GuildSettingsView(**values)  # type: ignore[arg-type]


def _service(cache: object, **overrides: object) -> RateLimitService:
    settings = _settings(**overrides)
    context = ServiceContext(settings, cache=cache, clock=lambda: NOW)  # type: ignore[arg-type]
    return RateLimitService(context)


def test_limits_come_from_the_guild_when_it_has_them() -> None:
    service = _service(ScriptedCache(), **{"DEFAULT_RATE_LIMIT_PER_USER_PER_MINUTE": 3})
    guild = _guild_settings(rate_limit_per_user_per_minute=9, rate_limit_per_guild_per_minute=99)
    assert service.user_limit(guild) == 9
    assert service.guild_limit(guild) == 99


def test_limits_fall_back_to_the_deployment_defaults() -> None:
    service = _service(
        ScriptedCache(),
        **{
            "DEFAULT_RATE_LIMIT_PER_USER_PER_MINUTE": 4,
            "DEFAULT_RATE_LIMIT_PER_GUILD_PER_MINUTE": 40,
        },
    )
    assert service.user_limit(_guild_settings()) == 4
    assert service.guild_limit(_guild_settings()) == 40


async def test_allowed_request_counts_both_windows() -> None:
    cache = ScriptedCache()
    service = _service(cache)
    decision = await service.check(user_id=7, guild_id=1234, settings=_guild_settings())
    assert decision.allowed is True
    assert [call["keys"][0].split(":")[-3] for call in cache.script_calls] == ["user", "guild"]
    assert all(call["name"] == "sliding_window_limiter" for call in cache.script_calls)
    assert len(cache.script_calls[0]["args"]) == 4


async def test_direct_messages_only_check_the_member_window() -> None:
    cache = ScriptedCache()
    service = _service(cache)
    await service.check(user_id=7, guild_id=None, settings=_guild_settings(guild_id=None))
    assert len(cache.script_calls) == 1
    assert cache.script_calls[0]["keys"][0].split(":")[-3] == "user"


async def test_refused_member_window_does_not_consume_the_guild_allowance() -> None:
    cache = ScriptedCache(allow=False)
    service = _service(cache)
    with pytest.raises(RateLimitedError) as excinfo:
        await service.ensure_allowed(user_id=7, guild_id=1234, settings=_guild_settings())
    assert excinfo.value.retry_after_seconds == 7.0
    assert len(cache.script_calls) == 1, (
        "the guild window must not be charged for a refused request"
    )


async def test_window_key_includes_the_scope_and_window_length() -> None:
    cache = ScriptedCache()
    service = _service(cache)
    await service.check(user_id=7, guild_id=1234, settings=_guild_settings())
    key = cache.script_calls[0]["keys"][0]
    assert key.startswith("thesun-test:ratelimit:user:7:60")
    assert cache.script_calls[0]["args"][1] == 60_000


async def test_disabled_limiting_skips_the_cache_entirely() -> None:
    cache = ScriptedCache()
    service = _service(cache, RATE_LIMIT_ENABLED="false")
    decision = await service.check(user_id=7, guild_id=1234, settings=_guild_settings())
    assert decision.allowed is True
    assert cache.script_calls == []


async def test_cache_outage_fails_open_and_is_counted() -> None:
    service = _service(FailingCacheBackend())
    decision = await service.check(user_id=7, guild_id=1234, settings=_guild_settings())
    assert decision.allowed is True
    assert decision.used == 0


async def test_script_failure_is_translated_not_leaked() -> None:
    class BrokenCache(ScriptedCache):
        async def run_script(
            self, name: str, *, keys: Sequence[str], args: Sequence[str | int]
        ) -> Any:
            raise CacheUnavailableError("simulated outage")

    service = _service(BrokenCache())
    decision = await service.check(user_id=7, guild_id=1234, settings=_guild_settings())
    assert decision.allowed is True, "losing Redis must not block members"
