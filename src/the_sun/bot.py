"""Discord runtime.

This is the only module that talks to Discord's gateway. It builds the client,
loads every command cog, publishes the command tree and turns exceptions into
safe user-facing replies with a correlation id the operator can grep for.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from the_sun.cogs import discover_cog_modules, import_cog_module
from the_sun.config import Settings, require_valid_settings
from the_sun.errors import (
    InvalidInputError,
    PermissionDeniedError,
    ProviderError,
    RateLimitedError,
    TheSunError,
)
from the_sun.logging_setup import (
    bind_correlation_id,
    configure_logging,
    current_correlation_id,
    new_correlation_id,
)
from the_sun.observability.health import HealthServer
from the_sun.services import (
    AIService,
    GuildSettingsView,
    HealthService,
    PermissionService,
    RetentionService,
    ServiceContext,
    SettingsService,
    UsageService,
    build_service_context,
)

__all__ = [
    "REQUIRED_BOT_PERMISSIONS",
    "SunBot",
    "build_intents",
    "describe_command_error",
    "invite_url",
    "run_bot",
]

logger = logging.getLogger(__name__)

#: Bot permissions the command surface actually needs. Message history and
#: attachment permissions support summarising and long answers; nothing here
#: grants moderation powers.
REQUIRED_BOT_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    use_application_commands=True,
)


@dataclass(frozen=True, slots=True)
class CommandFailure:
    """How a failed command should be reported."""

    message: str
    ephemeral: bool = True
    log_level: int = logging.ERROR
    code: str = "internal_error"


def build_intents(settings: Settings) -> discord.Intents:
    """Intents assembled from configuration.

    Slash commands and message context menus work without any privileged intent.
    Message content is only requested when the operator says it is enabled, since
    summarising other members' messages needs it.
    """
    intents = discord.Intents.none()
    intents.guilds = True
    if settings.discord_message_content_intent:
        intents.message_content = True
    return intents


def describe_command_error(error: BaseException) -> CommandFailure:
    """Map an exception onto a user-facing response.

    Deliberately pure so the mapping can be tested without a Discord connection.
    Internal details never reach the user; they are logged instead.
    """
    if isinstance(error, app_commands.CommandInvokeError):
        return describe_command_error(error.original)
    if isinstance(error, PermissionDeniedError):
        return CommandFailure(
            message=error.public_message, log_level=logging.WARNING, code="permission_denied"
        )
    if isinstance(error, InvalidInputError):
        return CommandFailure(
            message=error.public_message, log_level=logging.INFO, code="invalid_input"
        )
    if isinstance(error, RateLimitedError):
        suffix = (
            f" Try again in about {int(error.retry_after_seconds)}s."
            if error.retry_after_seconds
            else ""
        )
        return CommandFailure(
            message=f"{error.public_message}{suffix}",
            log_level=logging.INFO,
            code="rate_limited",
        )
    if isinstance(error, ProviderError):
        return CommandFailure(message=error.public_message, code=type(error).__name__)
    if isinstance(error, TheSunError):
        return CommandFailure(message=error.public_message, code=type(error).__name__)
    if isinstance(error, app_commands.CommandOnCooldown):
        return CommandFailure(
            message=f"That command is cooling down. Try again in {error.retry_after:.0f}s.",
            log_level=logging.INFO,
            code="cooldown",
        )
    if isinstance(error, app_commands.MissingPermissions):
        return CommandFailure(
            message="You do not have permission to run that command.",
            log_level=logging.WARNING,
            code="missing_permissions",
        )
    if isinstance(error, app_commands.BotMissingPermissions):
        return CommandFailure(
            message="I am missing a permission I need for that command.",
            code="bot_missing_permissions",
        )
    if isinstance(error, app_commands.CheckFailure):
        return CommandFailure(
            message="That command cannot be used here.",
            log_level=logging.WARNING,
            code="check_failed",
        )
    return CommandFailure(
        message=(
            "Something went wrong while handling that command. "
            "The details are in my logs for whoever runs me."
        ),
        code="unexpected_error",
    )


def invite_url(application_id: int) -> str:
    """The install URL for this application, including its real id."""
    return discord.utils.oauth_url(
        application_id,
        permissions=REQUIRED_BOT_PERMISSIONS,
        scopes=("bot", "applications.commands"),
    )


class SunBot(commands.Bot):
    """The Sun's Discord client."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(settings),
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
        )
        self.settings = settings
        self.service_context: ServiceContext = build_service_context(settings)
        self.settings_service = SettingsService(self.service_context)
        self.usage_service = UsageService(self.service_context)
        self.ai_service = AIService(self.service_context)
        self.conversation_service = self.ai_service.conversations
        self.memory_service = self.ai_service.memories
        self.retention_service = RetentionService(self.service_context)
        self.health_service = HealthService(self.service_context)
        self.permissions = PermissionService()
        self._retention_task: asyncio.Task[None] | None = None
        self.health_server: HealthServer | None = (
            HealthServer(
                self.service_context,
                host=settings.health_host,
                port=settings.health_port,
            )
            if settings.metrics_enabled
            else None
        )
        self._direct_message_settings: GuildSettingsView | None = None

    # ------------------------------------------------------------------ #
    # Wiring
    # ------------------------------------------------------------------ #
    @property
    def direct_message_settings(self) -> GuildSettingsView:
        """Defaults used for direct messages, where no guild row exists.

        Built from configuration by the settings service so the values live in one
        place instead of being duplicated inside commands.
        """
        if self._direct_message_settings is None:
            self._direct_message_settings = self.settings_service.direct_message_defaults()
        return self._direct_message_settings

    async def setup_hook(self) -> None:
        """Load cogs, start the background work, then publish the command tree."""
        loaded = await self.load_cogs()
        logger.info("cogs loaded", extra={"cogs": loaded})
        if self.health_server is not None:
            await self.health_server.start()
        self.start_retention_task()
        published = await self.sync_commands()
        logger.info("commands published", extra={"count": published})

    # ------------------------------------------------------------------ #
    # Background work
    # ------------------------------------------------------------------ #
    def start_retention_task(self) -> asyncio.Task[None] | None:
        """Run the retention purge periodically, when it is configured."""
        interval = self.retention_service.interval_hours
        if interval <= 0 or self._retention_task is not None:
            return None
        self._retention_task = asyncio.create_task(self._retention_loop(), name="retention")
        return self._retention_task

    async def _retention_loop(self) -> None:
        """Purge expired rows now and then, until the bot shuts down."""
        interval_seconds = self.retention_service.interval_hours * 3600
        while not self.is_closed():
            try:
                await self.retention_service.purge()
            except TheSunError as exc:
                logger.warning("retention purge failed", extra={"error": str(exc)})
            await asyncio.sleep(interval_seconds)

    async def load_cogs(self) -> list[str]:
        """Import and set up every command module in :mod:`the_sun.cogs`."""
        loaded: list[str] = []
        for module_name in discover_cog_modules():
            module = import_cog_module(module_name)
            setup = getattr(module, "setup", None)
            if setup is None:
                raise RuntimeError(f"cog module {module_name} does not define setup()")
            await setup(self)
            loaded.append(module_name)
        return loaded

    async def sync_commands(self) -> int:
        """Publish commands globally, or instantly to the development guild."""
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        return len(synced)

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #
    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Report every command failure once, safely and traceably."""
        failure = describe_command_error(error)
        correlation_id = current_correlation_id()
        if correlation_id is None:
            correlation_id = new_correlation_id()
            bind_correlation_id(correlation_id)
        logger.log(
            failure.log_level,
            "command failed",
            exc_info=error if failure.log_level >= logging.ERROR else None,
            extra={
                "command": _command_name(interaction),
                "guild_id": interaction.guild_id,
                "channel_id": interaction.channel_id,
                "user_id": interaction.user.id if interaction.user else None,
                "failure_code": failure.code,
            },
        )
        try:
            await self._send_failure(interaction, failure, correlation_id)
        except discord.HTTPException as exc:  # pragma: no cover - transport level
            logger.warning("could not deliver the failure message", extra={"error": str(exc)})

    async def _send_failure(
        self, interaction: discord.Interaction, failure: CommandFailure, correlation_id: str
    ) -> None:
        content = f"{failure.message}\n-# reference: `{correlation_id}`"
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=failure.ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=failure.ephemeral)

    async def close(self) -> None:
        """Release Discord, HTTP, database and cache resources."""
        try:
            if self._retention_task is not None:
                self._retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._retention_task
                self._retention_task = None
            if self.health_server is not None:
                await self.health_server.stop()
            await self.service_context.aclose()
        finally:
            await super().close()


def _command_name(interaction: discord.Interaction) -> str | None:
    command = getattr(interaction, "command", None)
    if command is None:
        return None
    return getattr(command, "qualified_name", None) or getattr(command, "name", None)


def run_bot(settings: Settings) -> None:
    """Validate configuration and run the bot until it is stopped."""
    require_valid_settings(settings)
    configure_logging(settings.log_level, json_output=settings.log_json)
    bot = SunBot(settings)
    bot.run(settings.discord_token_value, log_handler=None)
