"""Discord runtime pieces that can be verified without a gateway connection."""

from __future__ import annotations

import json
import logging
from typing import Any

import discord
from discord import app_commands

from the_sun.bot import (
    REQUIRED_BOT_PERMISSIONS,
    SunBot,
    build_intents,
    describe_command_error,
    invite_url,
)
from the_sun.cogs import discover_cog_modules, import_cog_module
from the_sun.config import Settings
from the_sun.errors import (
    InvalidInputError,
    PermissionDeniedError,
    ProviderCircuitOpenError,
    ProviderTimeoutError,
    RateLimitedError,
)


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


# --------------------------------------------------------------------------- #
# Intents and installation
# --------------------------------------------------------------------------- #
def test_intents_request_only_what_is_configured() -> None:
    without_content = build_intents(_settings(DISCORD_MESSAGE_CONTENT_INTENT="false"))
    assert without_content.guilds is True
    assert without_content.message_content is False
    assert without_content.members is False
    assert without_content.presences is False

    assert build_intents(_settings(DISCORD_MESSAGE_CONTENT_INTENT="true")).message_content is True


def test_invite_url_is_built_from_the_real_application_id() -> None:
    url = invite_url(123456789012345678)
    assert "client_id=123456789012345678" in url
    assert "scope=bot+applications.commands" in url
    assert f"permissions={REQUIRED_BOT_PERMISSIONS.value}" in url


def test_required_permissions_do_not_grant_moderation() -> None:
    assert REQUIRED_BOT_PERMISSIONS.administrator is False
    assert REQUIRED_BOT_PERMISSIONS.kick_members is False
    assert REQUIRED_BOT_PERMISSIONS.manage_messages is False
    assert REQUIRED_BOT_PERMISSIONS.read_message_history is True
    assert REQUIRED_BOT_PERMISSIONS.send_messages is True


# --------------------------------------------------------------------------- #
# Error reporting
# --------------------------------------------------------------------------- #
def test_permission_failures_tell_the_user_what_is_wrong() -> None:
    failure = describe_command_error(PermissionDeniedError("nope"))
    assert failure.ephemeral is True
    assert failure.code == "permission_denied"
    assert failure.log_level == logging.WARNING
    assert "permission" in failure.message.lower()


def test_invalid_input_is_not_logged_as_an_error() -> None:
    failure = describe_command_error(InvalidInputError("bad value"))
    assert failure.log_level == logging.INFO
    assert failure.code == "invalid_input"


def test_rate_limits_include_the_retry_delay() -> None:
    error = RateLimitedError("slow down")
    error.retry_after_seconds = 12.0
    failure = describe_command_error(error)
    assert "12s" in failure.message
    assert failure.code == "rate_limited"


def test_provider_errors_use_their_public_message() -> None:
    failure = describe_command_error(ProviderTimeoutError("took too long"))
    assert failure.message == ProviderTimeoutError.public_message
    assert failure.code == "ProviderTimeoutError"


def test_circuit_open_errors_are_reported_as_unavailable() -> None:
    assert (
        "temporarily unavailable"
        in describe_command_error(ProviderCircuitOpenError("all down")).message
    )


async def _probe_callback(interaction: discord.Interaction) -> None:  # pragma: no cover
    return None


def test_wrapped_command_errors_are_unwrapped() -> None:
    command: app_commands.Command[Any, ..., Any] = app_commands.Command(
        name="probe", description="probe", callback=_probe_callback
    )
    wrapped = app_commands.CommandInvokeError(command, ProviderTimeoutError("inner failure"))
    failure = describe_command_error(wrapped)
    assert failure.code == "ProviderTimeoutError"
    assert failure.message == ProviderTimeoutError.public_message


def test_unexpected_errors_never_leak_internal_details() -> None:
    failure = describe_command_error(ZeroDivisionError("division by zero at line 42"))
    assert failure.code == "unexpected_error"
    assert "division by zero" not in failure.message
    assert failure.log_level == logging.ERROR


def test_discord_check_failures_are_explained() -> None:
    assert describe_command_error(app_commands.CheckFailure("nope")).code == "check_failed"
    missing = app_commands.MissingPermissions(["manage_guild"])
    assert describe_command_error(missing).code == "missing_permissions"


# --------------------------------------------------------------------------- #
# Cogs and wiring
# --------------------------------------------------------------------------- #
def test_cog_modules_are_discoverable_and_importable() -> None:
    for module_name in discover_cog_modules():
        module = import_cog_module(module_name)
        assert hasattr(module, "setup"), f"{module_name} must expose setup()"


def test_cog_discovery_skips_shared_helpers() -> None:
    assert "the_sun.cogs.base" not in discover_cog_modules()


async def test_bot_wires_services_and_loads_every_cog() -> None:
    bot = SunBot(_settings())
    try:
        assert bot.service_context.settings is bot.settings
        assert bot.direct_message_settings.guild_id is None
        assert bot.direct_message_settings.ephemeral_responses is True
        assert bot.direct_message_settings.history_length == 20
        assert bot.health_server is None, "the health endpoint is opt-in"
        assert bot.intents.guilds is True

        loaded = await bot.load_cogs()
        assert loaded == discover_cog_modules()
        assert len(bot.tree.get_commands()) >= 1, "every command module registers commands"
    finally:
        await bot.service_context.aclose()


async def test_health_server_is_enabled_by_configuration() -> None:
    bot = SunBot(_settings(METRICS_ENABLED="true", HEALTH_PORT="9099"))
    try:
        assert bot.health_server is not None
    finally:
        await bot.service_context.aclose()


async def test_direct_message_defaults_follow_configuration() -> None:
    bot = SunBot(_settings(DEFAULT_HISTORY_LENGTH="5"))
    try:
        assert bot.direct_message_settings.history_length == 5
    finally:
        await bot.service_context.aclose()
