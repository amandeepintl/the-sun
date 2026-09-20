"""Translating text a member supplies."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.db.models import MessageKind, UsageSurface
from the_sun.services.prompts import message_instruction, translate_instruction

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["TRANSLATE_MENU_NAME", "TranslateCog"]

logger = logging.getLogger(__name__)

#: Name of the message context menu this cog publishes.
TRANSLATE_MENU_NAME = "Translate this message"


class TranslateCog(SunCog):
    """The ``/translate`` command and its message context menu."""

    def __init__(self, bot: SunBot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(
            app_commands.ContextMenu(name=TRANSLATE_MENU_NAME, callback=self.translate_message)
        )

    @app_commands.command(
        name="translate",
        description="Translate text you provide into another language.",
    )
    @app_commands.describe(
        text="The text to translate. Paste it, or reply to a message and paste its text.",
        target_language="The language to translate into, for example 'Dutch' or 'ja'.",
        source_language="The language of the text, when it is not obvious.",
    )
    async def translate(
        self,
        interaction: discord.Interaction,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> None:
        await self.respond(
            interaction,
            command="translate",
            kind=MessageKind.TRANSLATE,
            instruction=translate_instruction(
                text, target_language=target_language, source_language=source_language
            ),
        )

    async def translate_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Translate a real message, asking the model to detect the target language."""
        settings = await self.ensure_allowed(interaction)
        await self.respond(
            interaction,
            command="translate",
            kind=MessageKind.TRANSLATE,
            instruction=message_instruction(
                message.content or "",
                author=message.author.display_name,
                request=(
                    "Translate it into the language the member who asked normally writes in, "
                    "or into English when that is unclear. State which language you chose."
                ),
            ),
            settings=settings,
            surface=UsageSurface.CONTEXT_MENU,
            discord_message_id=message.id,
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(TranslateCog(bot))
