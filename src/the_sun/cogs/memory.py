"""Explicit memory: what the member asked The Sun to remember.

Nothing is remembered automatically. Every entry here exists because a member
saved it, and the member can list, delete or clear them at any time. Inside a
server an entry can be scoped to that server (the default) or saved for every
context ("everywhere"), which is also the only scope a direct message has.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.errors import InvalidInputError

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["MEMORY_MENU_NAME", "MemoryCog", "MemoryScope"]

logger = logging.getLogger(__name__)

#: Name of the message context menu this cog publishes.
MEMORY_MENU_NAME = "Save to my memories"

#: ``scope`` option values. "this_server" is stored against the guild; the other
#: follows the member everywhere.
MemoryScope = Literal["this_server", "everywhere"]


class ClearConfirmView(discord.ui.View):
    """Confirms before deleting saved memories."""

    def __init__(
        self,
        cog: MemoryCog,
        *,
        guild_id: int | None,
        include_global: bool,
    ) -> None:
        super().__init__(timeout=60)
        self._cog = cog
        self._guild_id = guild_id
        self._include_global = include_global

    @discord.ui.button(label="Delete them", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        removed = await self._cog.memories.clear(
            user_id=interaction.user.id,
            guild_id=self._guild_id,
            include_global=self._include_global,
        )
        await interaction.response.edit_message(
            content=f"Deleted {removed} saved memory entr{'y' if removed == 1 else 'ies'}.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Keep them", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Nothing was deleted.", view=None)
        self.stop()


class MemoryModal(discord.ui.Modal, title="Save a memory"):
    """Collects a key and a value for the ``Save to my memories`` context menu."""

    key: discord.ui.TextInput[MemoryModal] = discord.ui.TextInput(
        label="Key",
        placeholder="timezone",
        max_length=64,
    )
    value: discord.ui.TextInput[MemoryModal] = discord.ui.TextInput(
        label="What should The Sun remember?",
        placeholder="I work in Europe/Amsterdam and prefer metric units.",
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: MemoryCog, *, content: str | None = None) -> None:
        super().__init__()
        self._cog = cog
        if content:
            self.value.default = " ".join(content.split())[:1000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        settings = await self._cog.ensure_allowed(interaction)
        if not settings.memory_enabled:
            raise InvalidInputError(
                "memory is disabled in this guild",
                public_message="Saving memories is turned off in this server.",
            )
        entry = await self._cog.memories.save(
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            key=self.key.value,
            value=self.value.value,
        )
        await self._cog.reply(
            interaction,
            f"Saved **{entry.key}** for {'this server' if entry.guild_id else 'everywhere'}. "
            "`/memory list` shows everything I know.",
            ephemeral=True,
        )


class MemoryCog(SunCog):
    """The ``/memory`` command group and its message context menu."""

    group = app_commands.Group(
        name="memory", description="Manage what The Sun remembers about you."
    )

    def __init__(self, bot: SunBot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(
            app_commands.ContextMenu(name=MEMORY_MENU_NAME, callback=self.save_from_message)
        )

    @group.command(name="save", description="Ask The Sun to remember a fact about you.")
    @app_commands.describe(
        key="A short name for the fact, for example 'timezone'.",
        value="What The Sun should remember.",
        scope="Where the memory applies.",
    )
    async def save(
        self,
        interaction: discord.Interaction,
        key: str,
        value: str,
        scope: MemoryScope = "this_server",
    ) -> None:
        settings = await self.guild_settings(interaction)
        if not settings.memory_enabled:
            raise InvalidInputError(
                "memory is disabled in this guild",
                public_message="Saving memories is turned off in this server.",
            )
        if scope == "everywhere" or interaction.guild_id is None:
            guild_id = None
        else:
            guild_id = interaction.guild_id
        entry = await self.memories.save(
            user_id=interaction.user.id, guild_id=guild_id, key=key, value=value
        )
        where = "every server and direct message" if entry.guild_id is None else "this server"
        await self.reply(
            interaction,
            f"Saved **{entry.key}** for {where}. `/memory list` shows everything I know.",
            ephemeral=True,
        )

    @group.command(name="list", description="List everything you asked The Sun to remember.")
    async def list_entries(self, interaction: discord.Interaction) -> None:
        entries = await self.memories.list_for_user(
            interaction.user.id, guild_id=interaction.guild_id
        )
        if not entries:
            await self.reply(
                interaction,
                "You have no saved memories. `/memory save` adds one, and I never remember "
                "anything on my own.",
                ephemeral=True,
            )
            return
        lines = []
        for entry in entries:
            scope = "everywhere" if entry.guild_id is None else "this server"
            lines.append(f"**{entry.key}** · `{scope}`\n{entry.value}")
        await self.reply(interaction, "\n\n".join(lines), ephemeral=True)

    @group.command(name="delete", description="Forget one saved memory.")
    @app_commands.describe(key="The key shown by /memory list.")
    async def delete(self, interaction: discord.Interaction, key: str) -> None:
        removed = await self.memories.delete(
            user_id=interaction.user.id, guild_id=interaction.guild_id, key=key
        )
        content = f"Forgot **{key}**." if removed else f"I had nothing saved under **{key}**."
        await self.reply(interaction, content, ephemeral=True)

    @group.command(name="clear", description="Delete every memory you have saved.")
    @app_commands.describe(include_everywhere="Also delete memories saved for every server.")
    async def clear(
        self, interaction: discord.Interaction, include_everywhere: bool = False
    ) -> None:
        everywhere = include_everywhere or interaction.guild_id is None
        view = ClearConfirmView(self, guild_id=interaction.guild_id, include_global=everywhere)
        scope_note = "everywhere" if everywhere else "in this server"
        await interaction.response.send_message(
            f"This deletes every memory you saved {scope_note}. Continue?",
            view=view,
            ephemeral=True,
        )

    async def save_from_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Save text from a real message, with the member choosing the key."""
        await self.ensure_allowed(interaction)
        await interaction.response.send_modal(MemoryModal(self, content=message.content))


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(MemoryCog(bot))
