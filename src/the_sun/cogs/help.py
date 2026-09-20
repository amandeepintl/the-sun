"""Help and installation.

The help text is generated from the command tree that is actually published, so it
can never drift from the commands users have: renaming or adding a command changes
this output automatically. ``/invite`` prints the real install URL for this
application, built from its own id.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun import __version__
from the_sun.bot import invite_url
from the_sun.cogs.base import SunCog
from the_sun.errors import TheSunError

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["HelpCog", "help_embed"]

logger = logging.getLogger(__name__)

#: How many top-level slash commands one help page lists.
MAX_LISTED = 25

#: Anything the command tree can contain.
PublishedCommand = app_commands.Command | app_commands.Group | app_commands.ContextMenu


def help_embed(commands: Sequence[PublishedCommand]) -> discord.Embed:
    """Describe the commands and menus this bot actually publishes."""
    ordered = list(commands)
    slash_commands = [entry for entry in ordered if not isinstance(entry, app_commands.ContextMenu)]
    menus = [entry for entry in ordered if isinstance(entry, app_commands.ContextMenu)]

    embed = discord.Embed(title="The Sun · commands", colour=discord.Colour.orange())
    for command in slash_commands[:MAX_LISTED]:
        if isinstance(command, app_commands.Group):
            children = "\n".join(
                f"`/{command.name} {child.name}` · {child.description}"
                for child in command.commands
            )
            embed.add_field(name=f"/{command.name}", value=children[:1024], inline=False)
        else:
            embed.add_field(name=f"/{command.name}", value=command.description or "—", inline=False)
    if menus:
        embed.add_field(
            name="Message context menus",
            value="\n".join(f"**{menu.name}** · right-click any message" for menu in menus),
            inline=False,
        )
    return embed


class HelpCog(SunCog):
    """The ``/help`` and ``/invite`` commands."""

    @app_commands.command(name="help", description="List everything The Sun can do.")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = help_embed(self.bot.tree.get_commands())
        embed.add_field(
            name="About",
            value=(
                f"version `{__version__}` · providers: "
                + ", ".join(f"`{name}`" for name in self.context.providers.names())
                + f" · default: `{self.context.default_provider_name}`\n"
                "Every answer comes from the configured provider; the usage, memory and privacy "
                "commands show exactly what is stored."
            ),
            inline=False,
        )
        await self.reply(interaction, "", ephemeral=True, embed=embed)

    @app_commands.command(
        name="invite", description="Get the link to install The Sun in another server."
    )
    async def invite(self, interaction: discord.Interaction) -> None:
        if self.bot.application_id is None:
            raise TheSunError(
                "the application id is not available yet",
                public_message="I am still starting up; try again in a moment.",
            )
        url = invite_url(self.bot.application_id)
        await self.reply(
            interaction,
            "Install The Sun in another server with this link:\n" + url,
            ephemeral=True,
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(HelpCog(bot))
