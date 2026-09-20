"""The ``Ask The Sun`` message context menu.

Right-clicking any real message opens a short modal for an optional instruction,
so the same entry point covers "what does this mean?", "rewrite this" and "answer
the question in this message".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.db.models import MessageKind, UsageSurface
from the_sun.services.prompts import message_instruction

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["ASK_MENU_NAME", "AskModal", "ContextMenuCog"]

logger = logging.getLogger(__name__)

#: Name of the message context menu this cog publishes.
ASK_MENU_NAME = "Ask The Sun"


class AskModal(discord.ui.Modal, title="Ask The Sun about this message"):
    """Collects the optional instruction for the selected message."""

    instruction: discord.ui.TextInput[AskModal] = discord.ui.TextInput(
        label="What should I do with it?",
        placeholder="Left empty: explain it and answer anything it asks",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: SunCog, message: discord.Message) -> None:
        super().__init__()
        self._cog = cog
        self._message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._cog.respond(
            interaction,
            command="ask_the_sun",
            kind=MessageKind.CONTEXT_ACTION,
            instruction=message_instruction(
                self._message.content or "",
                author=self._message.author.display_name,
                request=self.instruction.value or None,
            ),
            surface=UsageSurface.CONTEXT_MENU,
            discord_message_id=self._message.id,
        )


class ContextMenuCog(SunCog):
    """The ``Ask The Sun`` message context menu."""

    def __init__(self, bot: SunBot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(
            app_commands.ContextMenu(name=ASK_MENU_NAME, callback=self.ask_the_sun)
        )

    async def ask_the_sun(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """Open the instruction modal for the selected message."""
        await self.ensure_allowed(interaction)  # refuses outside allowed channels
        await interaction.response.send_modal(AskModal(self, message))


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(ContextMenuCog(bot))
