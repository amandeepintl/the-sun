"""Summarising real Discord conversations.

The transcript comes from Discord's own history endpoint, via discord.py, and only
the messages that were actually read are summarised. Nothing is cached: an edited
or deleted message must not survive in a stale transcript, so every summary reads
the channel again.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.db.models import MessageKind, UsageSurface
from the_sun.errors import InvalidInputError
from the_sun.services.prompts import TranscriptEntry, render_transcript, summarize_instruction

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["SUMMARIZE_MENU_NAME", "SummarizeCog", "collect_messages"]

#: Name of the message context menu this cog publishes.
SUMMARIZE_MENU_NAME = "Summarize from here"

logger = logging.getLogger(__name__)

#: Fewest messages a summary is allowed to be asked for.
MIN_MESSAGES = 2


async def collect_messages(
    channel: discord.abc.Messageable,
    *,
    limit: int,
    before: discord.Message | None = None,
) -> list[discord.Message]:
    """Read real messages from Discord, oldest first."""
    history = channel.history(limit=limit, before=before, oldest_first=False)
    collected = [message async for message in history]
    collected.reverse()
    return collected


def transcript_entries(
    messages: list[discord.Message],
) -> list[TranscriptEntry]:
    """Turn real Discord messages into quotable transcript lines."""
    entries: list[TranscriptEntry] = []
    for message in messages:
        entries.append(
            TranscriptEntry(
                author=message.author.display_name,
                content=message.content or "",
                timestamp=message.created_at,
                is_bot=bool(message.author.bot),
                has_attachments=bool(message.attachments),
            )
        )
    return entries


class SummarizeCog(SunCog):
    """The ``/summarize`` command and its message context menu."""

    def __init__(self, bot: SunBot) -> None:
        super().__init__(bot)
        self.bot.tree.add_command(
            app_commands.ContextMenu(name=SUMMARIZE_MENU_NAME, callback=self.summarize_from_here)
        )

    def _require_message_content(self) -> None:
        """Summarising other members' messages needs the privileged intent."""
        if not self.bot.intents.message_content:
            raise InvalidInputError(
                "the message content intent is disabled",
                public_message=(
                    "I cannot read message text, so summaries are unavailable. "
                    "A server administrator must enable the **Message Content** intent "
                    "for this application in the Discord developer portal and restart me."
                ),
            )

    async def _summarize(
        self,
        interaction: discord.Interaction,
        *,
        channel: discord.abc.Messageable,
        limit: int,
        focus: str | None,
        before: discord.Message | None = None,
        command: str,
        surface: UsageSurface,
        discord_message_id: int | None = None,
    ) -> None:
        maximum = self.context.settings.summary_max_messages
        if limit < MIN_MESSAGES or limit > maximum:
            raise InvalidInputError(
                f"summary length {limit} outside {MIN_MESSAGES}..{maximum}",
                public_message=(
                    f"Ask for between {MIN_MESSAGES} and {maximum} messages to summarise."
                ),
            )
        self._require_message_content()
        settings = await self.ensure_allowed(interaction)
        await self.defer(interaction, ephemeral=settings.ephemeral_responses)

        messages = await collect_messages(channel, limit=limit, before=before)
        if len(messages) < MIN_MESSAGES:
            await self.reply(
                interaction,
                f"I could only find {len(messages)} message(s) there, which is not enough "
                "to summarise.",
                ephemeral=settings.ephemeral_responses,
            )
            return

        transcript = render_transcript(
            transcript_entries(messages),
            max_chars_per_message=self.context.settings.summary_max_chars_per_message,
        )
        instruction = summarize_instruction(
            transcript,
            message_count=len(messages),
            channel_name=getattr(channel, "name", None),
            focus=focus,
        )
        answer = await self.ai.respond(
            self.build_request(
                interaction,
                command=command,
                kind=MessageKind.SUMMARY,
                instruction=instruction,
                settings=settings,
                surface=surface,
                discord_message_id=discord_message_id,
            )
        )
        await self.deliver_answer(interaction, answer, ephemeral=settings.ephemeral_responses)

    @app_commands.command(
        name="summarize",
        description="Summarise the most recent messages in a channel.",
    )
    @app_commands.describe(
        count="How many recent messages to read.",
        channel="Channel to read; defaults to this one.",
        focus="What you care about, if you want the summary aimed at something.",
    )
    async def summarize(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, MIN_MESSAGES, 500] = 50,
        channel: discord.TextChannel | discord.Thread | None = None,
        focus: str | None = None,
    ) -> None:
        candidate: object = channel or interaction.channel
        if not isinstance(candidate, discord.abc.Messageable):
            raise InvalidInputError(
                "no readable channel",
                public_message="I cannot read messages here. Pick a text channel or thread.",
            )
        await self._summarize(
            interaction,
            channel=candidate,
            limit=int(count),
            focus=focus,
            command="summarize",
            surface=UsageSurface.SLASH_COMMAND,
        )

    async def summarize_from_here(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Summarise the conversation ending at the message that was right-clicked."""
        limit = min(self.context.settings.summary_max_messages, 50)
        settings = await self.ensure_allowed(interaction)
        await self.defer(interaction, ephemeral=settings.ephemeral_responses)
        self._require_message_content()

        channel = message.channel
        older = await collect_messages(channel, limit=limit, before=message)
        window = [*older, message]
        transcript = render_transcript(
            transcript_entries(window),
            max_chars_per_message=self.context.settings.summary_max_chars_per_message,
        )
        instruction = summarize_instruction(
            transcript,
            message_count=len(window),
            channel_name=getattr(channel, "name", None),
            focus=None,
        )
        answer = await self.ai.respond(
            self.build_request(
                interaction,
                command="summarize",
                kind=MessageKind.SUMMARY,
                instruction=instruction,
                settings=settings,
                surface=UsageSurface.CONTEXT_MENU,
                discord_message_id=message.id,
            )
        )
        await self.deliver_answer(interaction, answer, ephemeral=settings.ephemeral_responses)


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(SummarizeCog(bot))
