"""Explaining code and concepts, including any real message a member selects.

The message context menu is created and registered in the constructor rather than
through a decorator, because discord.py's decorator typing does not account for
the ``self`` of a cog method.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.db.models import MessageKind, UsageSurface
from the_sun.services.prompts import explain_instruction, message_instruction

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["EXPLAIN_MENU_NAME", "ExplainCog"]

logger = logging.getLogger(__name__)

#: Name of the message context menu this cog publishes.
EXPLAIN_MENU_NAME = "Explain this message"


class ExplainCog(SunCog):
    """The ``/explain`` command and its message context menu."""

    def __init__(self, bot: SunBot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(
            app_commands.ContextMenu(name=EXPLAIN_MENU_NAME, callback=self.explain_message)
        )

    @app_commands.command(
        name="explain",
        description="Explain a piece of code or a concept.",
    )
    @app_commands.describe(
        subject="The code or concept to explain.",
        language="Programming language, when it helps the explanation.",
    )
    async def explain(
        self,
        interaction: discord.Interaction,
        subject: str,
        language: str | None = None,
    ) -> None:
        await self.respond(
            interaction,
            command="explain",
            kind=MessageKind.EXPLAIN,
            instruction=explain_instruction(subject, language=language),
        )

    async def explain_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Explain whatever a member right-clicked."""
        settings = await self.ensure_allowed(interaction)
        await self.respond(
            interaction,
            command="explain",
            kind=MessageKind.EXPLAIN,
            instruction=message_instruction(
                message.content or "",
                author=message.author.display_name,
            ),
            settings=settings,
            surface=UsageSurface.CONTEXT_MENU,
            discord_message_id=message.id,
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(ExplainCog(bot))
