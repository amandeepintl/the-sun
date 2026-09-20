"""Privacy: see what is stored, take a copy, delete it.

Members can always see and remove everything The Sun holds about them. Reads here
never create anything - asking what is stored must not store more - and the export
is generated from the rows themselves, so it is exactly what is kept.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["PrivacyCog"]

logger = logging.getLogger(__name__)

#: Turns included in a personal export.
EXPORT_TURN_LIMIT = 500


class ForgetConfirmView(discord.ui.View):
    """Confirms before deleting everything a member has stored."""

    def __init__(self, cog: PrivacyCog) -> None:
        super().__init__(timeout=60)
        self._cog = cog

    @discord.ui.button(label="Delete everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        conversations, turns = await self._cog.conversations.delete_for_user(interaction.user.id)
        memories = await self._cog.memories.clear(
            user_id=interaction.user.id, guild_id=None, include_global=True
        )
        async with self._cog.context.unit_of_work() as uow:
            events = await uow.usage.purge_for_user(interaction.user.id)
            profile = await uow.users.delete(interaction.user.id)
        await interaction.response.edit_message(
            content=(
                f"Deleted {conversations} conversation(s), {turns} stored turn(s), "
                f"{memories} memor{'y' if memories == 1 else 'ies'} and {events} usage "
                f"record(s). Profile record: {'removed' if profile else 'nothing to remove'}. "
                "Nothing about you remains stored."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Keep everything", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Nothing was deleted.", view=None)
        self.stop()


class PrivacyCog(SunCog):
    """The ``/privacy`` command group."""

    group = app_commands.Group(
        name="privacy", description="See or delete what The Sun stores about you."
    )

    @group.command(name="show", description="Show what The Sun has stored about you.")
    async def show(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        summary = await self.bot.usage_service.summary(user_id=user_id)
        turns = await self.conversations.count_for_user(user_id)
        async with self.context.unit_of_work() as uow:
            memories = await uow.memories.count_for_user(user_id)
        guild_settings = await self.guild_settings(interaction)
        retention = (
            f"{guild_settings.history_retention_days} days in this server"
            if guild_settings.history_retention_days
            else "kept until you delete it"
        )
        await self.reply(
            interaction,
            "**What I store about you**\n"
            f"· {summary.invocations:,} usage records: command name, ids, tokens and latency, "
            f"not message text\n"
            f"· {turns:,} stored conversation turns, only from questions you asked me\n"
            f"· {memories} saved memor{'y' if memories == 1 else 'ies'} (global and per server)\n"
            f"· history retention: {retention}\n"
            f"· usage records expire after {self.context.settings.usage_retention_days} days\n\n"
            "I never read or store messages from a channel unless you ask me to summarise it, "
            "and `/privacy export` sends you your own rows as a file.",
            ephemeral=True,
        )

    @group.command(name="export", description="Download a copy of everything stored about you.")
    async def export(self, interaction: discord.Interaction) -> None:
        await self.defer(interaction, ephemeral=True)
        user_id = interaction.user.id
        summary = await self.bot.usage_service.summary(user_id=user_id)
        turns = await self.conversations.turns_for_user(user_id, limit=EXPORT_TURN_LIMIT)
        async with self.context.unit_of_work() as uow:
            global_memories = await uow.memories.list_for_user(
                user_id, guild_id=None, include_global=True
            )
            scoped_memories = (
                await uow.memories.list_for_user(
                    user_id, guild_id=interaction.guild_id, include_global=False
                )
                if interaction.guild_id
                else []
            )
        payload = {
            "requested_at": self.context.clock().isoformat(),
            "user_id": str(user_id),
            "usage": {
                "invocations": summary.invocations,
                "successful": summary.successful,
                "failed": summary.failed,
                "rate_limited": summary.rate_limited,
                "prompt_tokens": summary.prompt_tokens,
                "completion_tokens": summary.completion_tokens,
                "first_occurred_at": summary.first_occurred_at.isoformat()
                if summary.first_occurred_at
                else None,
                "last_occurred_at": summary.last_occurred_at.isoformat()
                if summary.last_occurred_at
                else None,
            },
            "memories": [
                {
                    "key": entry.key,
                    "value": entry.value,
                    "scope": f"guild:{entry.guild_id}" if entry.guild_id else "everywhere",
                    "created_at": entry.created_at.isoformat(),
                }
                for entry in [*global_memories, *scoped_memories]
            ],
            "turns": [
                {
                    "role": message.role.value,
                    "kind": message.kind.value,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                    "provider": message.provider,
                    "model": message.model,
                }
                for message in turns
            ],
        }
        buffer = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
        await interaction.followup.send(
            f"Everything stored about you ({len(payload['turns'])} turns, "
            f"{len(payload['memories'])} memories).",
            file=discord.File(buffer, filename=f"the-sun-export-{user_id}.json"),
            ephemeral=True,
        )

    @group.command(name="forget", description="Delete everything The Sun stores about you.")
    async def forget(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "This deletes your conversations, stored turns, saved memories and usage records "
            "from the database. It cannot be undone. Continue?",
            view=ForgetConfirmView(self),
            ephemeral=True,
        )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(PrivacyCog(bot))
