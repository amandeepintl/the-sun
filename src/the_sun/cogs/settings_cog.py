"""Per-guild configuration.

Every server configures itself here: which provider and model to use, its own
system prompt, how much history to send, whether members may save memories,
whether replies are private, its rate limits, its token allowance and how long
history is kept. Nothing in this file contains a guild id, a channel id or a role
id - all of those come from the interaction that ran the command.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.errors import InvalidInputError, TheSunError
from the_sun.services import GuildSettingsView

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["SettingsCog"]

logger = logging.getLogger(__name__)

#: Autocomplete value meaning "use the configured default for this level".
DEFAULT_SENTINEL = "-"
#: Longest system prompt a guild may set.
MAX_PROMPT_CHARS = 4000

#: Columns returned to their defaults by ``/settings reset``.
RESET_CHANGES: dict[str, Any] = {
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
}


class PromptModal(discord.ui.Modal, title="Server system prompt"):
    """Collects a guild's own instructions for the model."""

    prompt: discord.ui.TextInput[PromptModal] = discord.ui.TextInput(
        label="System prompt",
        placeholder="Leave empty to fall back to the deployment default.",
        required=False,
        max_length=MAX_PROMPT_CHARS,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: SettingsCog, current: str | None) -> None:
        super().__init__()
        self._cog = cog
        if current:
            self.prompt.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.prompt.value.strip()
        view = await self._cog.settings_service.update(
            interaction.guild_id or 0, {"system_prompt": text or None}
        )
        stored = "the deployment default" if view.system_prompt is None else "this server's prompt"
        await self._cog.reply(interaction, f"System prompt now uses {stored}.", ephemeral=True)


class ResetConfirmView(discord.ui.View):
    """Confirms before discarding every server override."""

    def __init__(self, cog: SettingsCog) -> None:
        super().__init__(timeout=60)
        self._cog = cog

    @discord.ui.button(label="Reset everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._cog.settings_service.update(interaction.guild_id or 0, RESET_CHANGES)
        await interaction.response.edit_message(
            content="All server settings are back to their defaults.", view=None
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Nothing was changed.", view=None)
        self.stop()


class SettingsCog(SunCog):
    """The ``/settings`` command group. Administrators only."""

    group = app_commands.Group(
        name="settings",
        description="Configure The Sun for this server.",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    # ------------------------------------------------------------------ #
    # Autocomplete, driven by what is actually configured and offered
    # ------------------------------------------------------------------ #
    async def provider_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = self.context.providers.names()
        choices = [app_commands.Choice(name="use the deployment default", value=DEFAULT_SENTINEL)]
        choices += [
            app_commands.Choice(name=name, value=name)
            for name in names
            if current.lower() in name.lower()
        ]
        return choices[:25]

    async def model_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        provider = getattr(interaction.namespace, "provider", None)
        if provider == DEFAULT_SENTINEL:
            provider = None
        try:
            models = await self.context.ai.list_models(provider)
        except TheSunError as exc:
            logger.info(
                "model catalogue unavailable during autocomplete",
                extra={"provider": provider, "error": str(exc)},
            )
            return [
                app_commands.Choice(
                    name="provider default (catalogue unavailable)", value=DEFAULT_SENTINEL
                )
            ]
        choices = [app_commands.Choice(name="provider default", value=DEFAULT_SENTINEL)]
        choices += [
            app_commands.Choice(name=info.id, value=info.id)
            for info in models
            if current.lower() in info.id.lower()
        ]
        return choices[:25]

    # ------------------------------------------------------------------ #
    # Reading the configuration
    # ------------------------------------------------------------------ #
    def _summary_embed(self, settings: GuildSettingsView) -> discord.Embed:
        base = self.context.settings
        rate_limits = self.ai.rate_limiter
        history_window = (
            settings.history_length if settings.guild_id else base.default_history_length
        )
        embed = discord.Embed(
            title="The Sun · server settings",
            colour=discord.Colour.gold(),
        )
        embed.add_field(
            name="Model",
            value=(
                f"provider: `{settings.ai_provider or self.context.default_provider_name}`"
                f"{'' if settings.ai_provider else ' (deployment default)'}\n"
                f"model: `{settings.ai_model or 'provider default'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Behaviour",
            value=(
                f"history window: {history_window}\n"
                f"memories: {'on' if settings.memory_enabled else 'off'}\n"
                f"private replies: {'yes' if settings.ephemeral_responses else 'no'}\n"
                f"system prompt: {'this server' if settings.system_prompt else 'deployment default'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Limits",
            value=(
                f"per member: {rate_limits.user_limit(settings)}/minute\n"
                f"per server: {rate_limits.guild_limit(settings)}/minute\n"
                f"daily tokens: {settings.daily_token_quota or 'unlimited'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Scope",
            value=(
                f"channels: "
                f"{_list_scope([f'<#{cid}>' for cid in settings.allowed_channel_ids], 'every channel')}\n"
                f"admin roles: "
                f"{_list_scope([f'<@&{rid}>' for rid in settings.admin_role_ids], 'Manage Server only')}\n"
                f"history retention: "
                f"{f'{settings.history_retention_days} days' if settings.history_retention_days else 'kept until deleted'}"
            ),
            inline=False,
        )
        if settings.guild_id:
            embed.set_footer(text=f"guild id {settings.guild_id}")
        return embed

    @group.command(name="show", description="Show this server's configuration.")
    async def show(self, interaction: discord.Interaction) -> None:
        settings = await self.ensure_admin(interaction)
        await self.reply(interaction, "", ephemeral=True, embed=self._summary_embed(settings))

    # ------------------------------------------------------------------ #
    # Writing the configuration
    # ------------------------------------------------------------------ #
    @group.command(name="model", description="Choose the provider and model for this server.")
    @app_commands.describe(
        provider="Provider to use; the first choice means the deployment default.",
        model="Model to use; the first choice means the provider's configured default.",
    )
    @app_commands.autocomplete(provider=provider_autocomplete, model=model_autocomplete)
    async def model(
        self,
        interaction: discord.Interaction,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        await self.ensure_admin(interaction)
        if provider is None and model is None:
            raise InvalidInputError(
                "no change was requested",
                public_message="Choose a provider or a model to change.",
            )
        changes: dict[str, Any] = {}
        if provider is not None:
            if provider != DEFAULT_SENTINEL and provider not in self.context.providers.names():
                raise InvalidInputError(
                    f"provider {provider!r} is not configured",
                    public_message=(
                        f"`{provider}` is not a configured provider. Configured providers: "
                        + ", ".join(f"`{name}`" for name in self.context.providers.names())
                    ),
                )
            changes["ai_provider"] = None if provider == DEFAULT_SENTINEL else provider
        if model is not None:
            changes["ai_model"] = None if model == DEFAULT_SENTINEL else model
        view = await self.settings_service.update(interaction.guild_id or 0, changes)
        await self.reply(
            interaction,
            f"Now using provider `{view.ai_provider or self.context.default_provider_name}` "
            f"and model `{view.ai_model or 'provider default'}`.",
            ephemeral=True,
        )

    @group.command(name="prompt", description="Set this server's own instructions for the model.")
    async def prompt(self, interaction: discord.Interaction) -> None:
        settings = await self.ensure_admin(interaction)
        await interaction.response.send_modal(PromptModal(self, settings.system_prompt))

    @group.command(name="history", description="How many previous turns are sent back each time.")
    @app_commands.describe(length="Number of stored turns, 1 to 200.")
    async def history(
        self, interaction: discord.Interaction, length: app_commands.Range[int, 1, 200]
    ) -> None:
        await self.ensure_admin(interaction)
        view = await self.settings_service.update(
            interaction.guild_id or 0, {"history_length": int(length)}
        )
        await self.reply(
            interaction, f"History window is now {view.history_length} turns.", ephemeral=True
        )

    @group.command(name="memory", description="Allow members to save and use memories here.")
    @app_commands.describe(enabled="Whether saved memories are stored and sent to the model.")
    async def memory(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.ensure_admin(interaction)
        await self.settings_service.update(interaction.guild_id or 0, {"memory_enabled": enabled})
        await self.reply(
            interaction,
            f"Memories are now {'enabled' if enabled else 'disabled'} in this server.",
            ephemeral=True,
        )

    @group.command(
        name="ephemeral", description="Whether answers are private to the asker by default."
    )
    @app_commands.describe(ephemeral="True makes replies visible only to whoever asked.")
    async def ephemeral(self, interaction: discord.Interaction, ephemeral: bool) -> None:
        await self.ensure_admin(interaction)
        await self.settings_service.update(
            interaction.guild_id or 0, {"ephemeral_responses": ephemeral}
        )
        await self.reply(
            interaction,
            "Replies are now private by default."
            if ephemeral
            else "Replies are now visible in the channel by default.",
            ephemeral=True,
        )

    @group.command(name="channels", description="Restrict The Sun to specific channels.")
    @app_commands.describe(
        mode="allow_all clears the list; add and remove edit it; list shows it.",
        channel="Channel to add or remove.",
    )
    async def channels(
        self,
        interaction: discord.Interaction,
        mode: Literal["allow_all", "add", "remove", "list"],
        channel: discord.TextChannel | discord.Thread | None = None,
    ) -> None:
        settings = await self.ensure_admin(interaction)
        allowed = list(settings.allowed_channel_ids)
        if mode == "allow_all":
            await self.settings_service.update(
                interaction.guild_id or 0, {"allowed_channel_ids": []}
            )
            await self.reply(interaction, "The Sun now works in every channel.", ephemeral=True)
            return
        if mode == "list":
            await self.reply(
                interaction,
                "Working in: " + _list_scope([f"<#{cid}>" for cid in allowed], "every channel"),
                ephemeral=True,
            )
            return
        if channel is None:
            raise InvalidInputError(
                "no channel was given",
                public_message="Pick a channel to add or remove.",
            )
        if mode == "add":
            if channel.id in allowed:
                raise InvalidInputError(
                    "channel is already allowed",
                    public_message=f"{channel.mention} is already allowed.",
                )
            allowed.append(channel.id)
        else:
            if channel.id not in allowed:
                raise InvalidInputError(
                    "channel is not in the list",
                    public_message=(
                        f"{channel.mention} is not in the list. While the list is empty every "
                        "channel is allowed."
                    ),
                )
            allowed.remove(channel.id)
        await self.settings_service.update(
            interaction.guild_id or 0, {"allowed_channel_ids": allowed}
        )
        await self.reply(
            interaction,
            f"{'Added' if mode == 'add' else 'Removed'} {channel.mention}. "
            f"Working in: {_list_scope([f'<#{cid}>' for cid in allowed], 'every channel')}",
            ephemeral=True,
        )

    @group.command(name="admins", description="Delegate administration to roles.")
    @app_commands.describe(
        mode="add and remove edit the list; clear empties it; list shows it.",
        role="Role to add or remove.",
    )
    async def admins(
        self,
        interaction: discord.Interaction,
        mode: Literal["add", "remove", "clear", "list"],
        role: discord.Role | None = None,
    ) -> None:
        settings = await self.ensure_admin(interaction)
        roles = list(settings.admin_role_ids)
        if mode == "list":
            await self.reply(
                interaction,
                _list_scope([f"<@&{role_id}>" for role_id in roles], "Manage Server only"),
                ephemeral=True,
            )
            return
        if mode == "clear":
            await self.settings_service.update(interaction.guild_id or 0, {"admin_role_ids": []})
            await self.reply(
                interaction, "Only Manage Server can administer The Sun now.", ephemeral=True
            )
            return
        if role is None:
            raise InvalidInputError(
                "no role was given", public_message="Pick a role to add or remove."
            )
        if mode == "add":
            if role.id in roles:
                raise InvalidInputError(
                    "role is already an administrator",
                    public_message=f"{role.mention} can already administer The Sun.",
                )
            roles.append(role.id)
        else:
            if role.id not in roles:
                raise InvalidInputError(
                    "role is not an administrator",
                    public_message=f"{role.mention} is not in the list.",
                )
            roles.remove(role.id)
        await self.settings_service.update(interaction.guild_id or 0, {"admin_role_ids": roles})
        await self.reply(
            interaction,
            f"{role.mention} {'can now' if mode == 'add' else 'can no longer'} administer The Sun.",
            ephemeral=True,
        )

    @group.command(name="ratelimit", description="Per-minute request limits for this server.")
    @app_commands.describe(
        per_user="Requests per member per minute.",
        per_guild="Requests per minute for the whole server.",
        clear="Remove both overrides and use the deployment defaults.",
    )
    async def ratelimit(
        self,
        interaction: discord.Interaction,
        per_user: app_commands.Range[int, 1, 600] | None = None,
        per_guild: app_commands.Range[int, 1, 6000] | None = None,
        clear: bool = False,
    ) -> None:
        await self.ensure_admin(interaction)
        changes: dict[str, Any] = {}
        if clear:
            changes = {
                "rate_limit_per_user_per_minute": None,
                "rate_limit_per_guild_per_minute": None,
            }
        else:
            if per_user is not None:
                changes["rate_limit_per_user_per_minute"] = int(per_user)
            if per_guild is not None:
                changes["rate_limit_per_guild_per_minute"] = int(per_guild)
            if not changes:
                raise InvalidInputError(
                    "no change was requested",
                    public_message="Give a limit to change, or set `clear` to true.",
                )
        view = await self.settings_service.update(interaction.guild_id or 0, changes)
        await self.reply(
            interaction,
            f"Limits: {self.ai.rate_limiter.user_limit(view)} per member per minute, "
            f"{self.ai.rate_limiter.guild_limit(view)} per minute for the server.",
            ephemeral=True,
        )

    @group.command(name="quota", description="Daily token allowance for this server.")
    @app_commands.describe(
        tokens="Tokens per day, counted from what providers report.",
        clear="Remove the allowance and leave usage unlimited.",
    )
    async def quota(
        self,
        interaction: discord.Interaction,
        tokens: app_commands.Range[int, 1000, 100000000] | None = None,
        clear: bool = False,
    ) -> None:
        await self.ensure_admin(interaction)
        if not clear and tokens is None:
            raise InvalidInputError(
                "no change was requested",
                public_message="Give a token allowance, or set `clear` to true.",
            )
        value = None if clear else int(tokens or 0)
        view = await self.settings_service.update(
            interaction.guild_id or 0, {"daily_token_quota": value}
        )
        await self.reply(
            interaction,
            f"Daily allowance is now {view.daily_token_quota or 'unlimited'} tokens.",
            ephemeral=True,
        )

    @group.command(name="retention", description="How long this server's stored history is kept.")
    @app_commands.describe(
        days="Days to keep stored turns.",
        clear="Keep history until it is deleted explicitly.",
    )
    async def retention(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 3650] | None = None,
        clear: bool = False,
    ) -> None:
        await self.ensure_admin(interaction)
        if not clear and days is None:
            raise InvalidInputError(
                "no change was requested",
                public_message="Give a number of days, or set `clear` to true.",
            )
        value = None if clear else int(days or 0)
        view = await self.settings_service.update(
            interaction.guild_id or 0, {"history_retention_days": value}
        )
        await self.reply(
            interaction,
            "History is kept until someone deletes it."
            if view.history_retention_days is None
            else f"History is deleted after {view.history_retention_days} days.",
            ephemeral=True,
        )

    @group.command(name="reset", description="Return every server setting to its default.")
    async def reset(self, interaction: discord.Interaction) -> None:
        await self.ensure_admin(interaction)
        await interaction.response.send_message(
            "This discards every override for this server. Continue?",
            view=ResetConfirmView(self),
            ephemeral=True,
        )


def _list_scope(values: list[str], empty: str) -> str:
    """Render an id list, or the placeholder used when it is empty."""
    if not values:
        return empty
    return ", ".join(str(value) for value in values)


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(SettingsCog(bot))
