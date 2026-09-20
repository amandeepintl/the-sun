"""Asking The Sun, starting over, and reading back what was stored.

The conversation is per member per channel, so this is the member's own thread:
``/new`` closes it and ``/history`` pages through exactly the turns written because
they asked something.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands

from the_sun.cogs.base import SunCog
from the_sun.db.models import Message, MessageKind, MessageRole
from the_sun.services.prompts import answer_instruction

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from the_sun.bot import SunBot

__all__ = ["ConversationCog", "HistoryView", "history_embed"]

logger = logging.getLogger(__name__)

#: Characters of one stored turn shown per history entry.
CONTENT_PREVIEW_CHARS = 300


def _preview(message: Message) -> str:
    """A short, single-line rendering of a stored turn."""
    body = " ".join(message.content.split())
    if len(body) > CONTENT_PREVIEW_CHARS:
        body = body[: CONTENT_PREVIEW_CHARS - 1].rstrip() + "\u2026"
    return body or "*(empty)*"


def history_embed(
    messages: Sequence[Message], *, page: int, total: int, name: str
) -> discord.Embed:
    """Render one page of stored turns."""
    embed = discord.Embed(title=f"Conversation history · {name}", colour=discord.Colour.orange())
    if not messages:
        embed.description = "Nothing has been stored in the active conversation yet."
    for message in messages:
        heading = "**You**" if message.role is MessageRole.USER else "**The Sun**"
        if message.kind is not MessageKind.CHAT:
            heading += f" · `{message.kind.value}`"
        embed.add_field(
            name=heading,
            value=f"-# <t:{int(message.created_at.timestamp())}:R>\n{_preview(message)}",
            inline=False,
        )
    embed.set_footer(text=f"page {page} · {total} stored turns")
    return embed


class HistoryView(discord.ui.View):
    """Pages through stored turns using the repository's keyset cursor."""

    def __init__(
        self,
        cog: SunCog,
        *,
        conversation_id: uuid.UUID,
        page_size: int,
        total: int,
        name: str,
        next_cursor: str | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._cog = cog
        self._conversation_id = conversation_id
        self._page_size = page_size
        self._total = total
        self._name = name
        # ``_cursors[i]`` is the cursor that produced page ``i``; page 0 has none.
        self._cursors: list[str | None] = [None]
        self._index = 0
        self._next_cursor = next_cursor
        self._sync()

    def _sync(self) -> None:
        self.older_button.disabled = self._next_cursor is None
        self.newer_button.disabled = self._index == 0

    async def _render(self, interaction: discord.Interaction) -> None:
        page = await self._cog.conversations.history_page(
            self._conversation_id, limit=self._page_size, cursor=self._cursors[self._index]
        )
        self._next_cursor = page.next_cursor
        self._sync()
        embed = history_embed(page.items, page=self._index + 1, total=self._total, name=self._name)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Older", style=discord.ButtonStyle.secondary)
    async def older_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        if self._next_cursor is not None:
            self._cursors.append(self._next_cursor)
            self._index = len(self._cursors) - 1
        await self._render(interaction)

    @discord.ui.button(label="Newer", style=discord.ButtonStyle.secondary)
    async def newer_button(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        self._index = max(0, self._index - 1)
        # The cursor that produced the page we just left is the one that leads back.
        following = self._index + 1
        self._next_cursor = self._cursors[following] if following < len(self._cursors) else None
        await self._render(interaction)


class ConversationCog(SunCog):
    """Direct questions, conversation resets and stored history."""

    @app_commands.command(
        name="ask",
        description="Ask The Sun a question, with this conversation's context.",
    )
    @app_commands.describe(
        prompt="What you want to ask.",
        visibility="Whether the answer is private to you or visible in this channel.",
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        visibility: Literal["private", "server"] = "private",
    ) -> None:
        await self.respond(
            interaction,
            command="ask",
            kind=MessageKind.CHAT,
            instruction=answer_instruction(prompt),
            visibility=visibility,
        )

    @app_commands.command(
        name="new",
        description="Start a fresh conversation; earlier turns stay stored and are not sent again.",
    )
    async def new(self, interaction: discord.Interaction) -> None:
        settings = await self.ensure_allowed(interaction)
        reset = await self.conversations.reset(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
        )
        content = (
            "Started a new conversation. Earlier turns are still stored - `/history` shows them."
            if reset
            else "There was no active conversation, so your next question starts one."
        )
        await self.reply(interaction, content, ephemeral=settings.ephemeral_responses)

    @app_commands.command(name="history", description="Show the turns stored in your conversation.")
    async def history(self, interaction: discord.Interaction) -> None:
        settings = await self.ensure_allowed(interaction)
        page_size = self.context.settings.history_page_size
        conversation = await self.conversations.current(
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
        )
        total = await self.conversations.count(conversation.id)
        page = await self.conversations.history_page(conversation.id, limit=page_size)
        name = interaction.user.display_name
        embed = history_embed(page.items, page=1, total=total, name=name)
        view = HistoryView(
            self,
            conversation_id=conversation.id,
            page_size=page_size,
            total=total,
            name=name,
            next_cursor=page.next_cursor,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed, view=view, ephemeral=settings.ephemeral_responses
            )
        else:
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=settings.ephemeral_responses
            )


async def setup(bot: SunBot) -> None:
    """Register this cog with the bot."""
    await bot.add_cog(ConversationCog(bot))
