"""Shared cog plumbing.

Commands stay thin: they collect input, call a service and render the result.
Everything repetitive about Discord interactions - deferring in time, resolving
settings, applying the channel allow-list, choosing a visibility, splitting long
answers - lives here so no individual command has to remember it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from the_sun.db.models import Memory, MessageKind, UsageSurface
from the_sun.services import (
    AIAnswer,
    AIRequest,
    AIService,
    ConversationService,
    GuildSettingsView,
    MemoryService,
    PermissionService,
    ServiceContext,
    SettingsService,
)
from the_sun.services.permissions import CallerContext

if TYPE_CHECKING:  # pragma: no cover
    from the_sun.bot import SunBot

__all__ = [
    "EMPTY_ANSWER_NOTICE",
    "MAX_MESSAGE_LENGTH",
    "PUBLIC_VISIBILITY",
    "SunCog",
    "discord_message_link",
    "split_for_discord",
    "to_caller_context",
]

logger = logging.getLogger(__name__)

#: Discord rejects message content longer than this.
MAX_MESSAGE_LENGTH = 2000
#: Shown when a provider returned an answer with no text in it.
EMPTY_ANSWER_NOTICE = "(the provider returned an empty answer)"
#: Value of the `visibility` option that makes a reply visible to the channel.
PUBLIC_VISIBILITY = "server"


def to_caller_context(interaction: discord.Interaction) -> CallerContext:
    """Translate a real interaction into the values the permission rules need."""
    member = interaction.user
    role_ids: frozenset[int] = frozenset()
    has_manage_guild = False
    is_owner = False
    if isinstance(member, discord.Member):
        role_ids = frozenset(role.id for role in member.roles)
        permissions = member.guild_permissions
        has_manage_guild = bool(permissions.manage_guild or permissions.administrator)
        is_owner = member.id == member.guild.owner_id
    return CallerContext(
        user_id=interaction.user.id,
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        role_ids=role_ids,
        has_manage_guild=has_manage_guild,
        is_guild_owner=is_owner,
    )


def discord_message_link(
    guild_id: int | None, channel_id: int | None, message_id: int | None
) -> str | None:
    """A clickable jump link for a real message, or ``None`` without guild context."""
    if guild_id is None or channel_id is None or message_id is None:
        return None
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def split_for_discord(content: str, *, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split an answer into deliverable messages.


    Splitting prefers paragraph boundaries, then line boundaries, and keeps fenced
    code blocks whole by closing a split fence and reopening it in the next
    message - so a long answer stays readable instead of becoming broken markdown.
    """
    text = content.strip()
    if not text:
        return [EMPTY_ANSWER_NOTICE]

    chunks: list[str] = []
    remaining = text
    fence_open = False
    while remaining:
        prefix = "```\n" if fence_open else ""
        available = limit - len(prefix)
        if len(remaining) <= available:
            chunks.append(prefix + remaining)
            break
        window = remaining[:available]
        cut = window.rfind("\n\n")
        if cut < available // 2:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = available
        piece = remaining[:cut].rstrip()
        remaining = remaining[cut:].lstrip("\n")
        # Every fence in the piece toggles the state; the chunk starts open when a
        # previous chunk left a block unfinished, because the prefix reopened it.
        state = fence_open
        for _ in range(piece.count("```")):
            state = not state
        if state:
            piece = f"{piece}\n```"
        fence_open = state
        chunks.append(prefix + piece)
    return chunks


class SunCog(commands.Cog):
    """Base class every command cog inherits from."""

    def __init__(self, bot: SunBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Services
    # ------------------------------------------------------------------ #
    @property
    def context(self) -> ServiceContext:
        return self.bot.service_context

    @property
    def settings_service(self) -> SettingsService:
        return self.bot.settings_service

    @property
    def permissions(self) -> PermissionService:
        return self.bot.permissions

    @property
    def ai(self) -> AIService:
        return self.bot.ai_service

    @property
    def conversations(self) -> ConversationService:
        return self.bot.conversation_service

    @property
    def memories(self) -> MemoryService:
        return self.bot.memory_service

    # ------------------------------------------------------------------ #
    # Settings and permission context
    # ------------------------------------------------------------------ #
    async def guild_settings(self, interaction: discord.Interaction) -> GuildSettingsView:
        """Settings for the interaction's guild, or defaults for direct messages."""
        guild_id = interaction.guild_id
        if guild_id is None:
            return self.bot.direct_message_settings
        return await self.settings_service.get(guild_id)

    async def ensure_allowed(self, interaction: discord.Interaction) -> GuildSettingsView:
        """Apply the channel allow-list; returns the settings when allowed."""
        settings = await self.guild_settings(interaction)
        self.permissions.ensure_channel_allowed(to_caller_context(interaction), settings)
        return settings

    async def ensure_admin(self, interaction: discord.Interaction) -> GuildSettingsView:
        """Resolve settings and refuse the caller unless they administer The Sun."""
        settings = await self.guild_settings(interaction)
        self.permissions.ensure_admin(to_caller_context(interaction), settings)
        return settings

    # ------------------------------------------------------------------ #
    # Interaction plumbing
    # ------------------------------------------------------------------ #
    async def defer(self, interaction: discord.Interaction, *, ephemeral: bool) -> None:
        """Acknowledge an interaction before doing slow work."""
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)

    async def reply(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool,
        embed: discord.Embed | None = None,
    ) -> None:
        """Send a reply, whether or not the interaction was deferred first."""
        if interaction.response.is_done():
            if embed is None:
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.followup.send(content, ephemeral=ephemeral, embeds=[embed])
        elif embed is None:
            await interaction.response.send_message(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral, embeds=[embed])

    # ------------------------------------------------------------------ #
    # The one path every AI command takes
    # ------------------------------------------------------------------ #
    def build_request(
        self,
        interaction: discord.Interaction,
        *,
        command: str,
        kind: MessageKind,
        instruction: str,
        settings: GuildSettingsView,
        surface: UsageSurface = UsageSurface.SLASH_COMMAND,
        use_history: bool = True,
        use_memories: bool = True,
        store_turns: bool = True,
        discord_message_id: int | None = None,
    ) -> AIRequest:
        """Describe one AI invocation for this interaction.

        Commands that defer themselves (because they read Discord history first)
        use this to build the request; :meth:`respond` uses it internally.
        """
        return AIRequest(
            command=command,
            instruction=instruction,
            user_id=interaction.user.id,
            settings=settings,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            kind=kind,
            surface=surface,
            discord_message_id=discord_message_id,
            use_history=use_history,
            use_memories=use_memories,
            store_turns=store_turns,
        )

    async def respond(
        self,
        interaction: discord.Interaction,
        *,
        command: str,
        kind: MessageKind,
        instruction: str,
        settings: GuildSettingsView | None = None,
        surface: UsageSurface = UsageSurface.SLASH_COMMAND,
        use_history: bool = True,
        use_memories: bool = True,
        store_turns: bool = True,
        visibility: str | None = None,
        discord_message_id: int | None = None,
    ) -> AIAnswer:
        """Defer, ask, and deliver an answer split across messages.

        Resolving the settings here (including the channel allow-list) means no
        command can accidentally skip the guild's policy. The provenance footer is
        built from what the provider actually reported.
        """
        resolved = settings or await self.ensure_allowed(interaction)
        ephemeral = resolved.ephemeral_responses
        if visibility == PUBLIC_VISIBILITY:
            ephemeral = False
        await self.defer(interaction, ephemeral=ephemeral)

        answer = await self.ai.respond(
            self.build_request(
                interaction,
                command=command,
                kind=kind,
                instruction=instruction,
                settings=resolved,
                surface=surface,
                discord_message_id=discord_message_id,
                use_history=use_history,
                use_memories=use_memories,
                store_turns=store_turns,
            )
        )
        await self.deliver_answer(interaction, answer, ephemeral=ephemeral)
        return answer

    async def deliver_answer(
        self, interaction: discord.Interaction, answer: AIAnswer, *, ephemeral: bool
    ) -> None:
        """Send an answer, splitting it and attaching a provenance footer."""
        chunks = split_for_discord(answer.content)
        footer = self.provenance(answer)
        appended = len(chunks[-1]) + len(footer) + 1 <= MAX_MESSAGE_LENGTH
        if appended:
            chunks[-1] = f"{chunks[-1]}\n{footer}"
        for index, chunk in enumerate(chunks):
            if index == 0 and not interaction.response.is_done():
                await interaction.response.send_message(chunk, ephemeral=ephemeral)
            else:
                await interaction.followup.send(chunk, ephemeral=ephemeral)
        if not appended:
            await interaction.followup.send(footer, ephemeral=ephemeral)

    @staticmethod
    def provenance(answer: AIAnswer) -> str:
        """A one-line note describing how the answer was produced."""
        parts = [f"provider {answer.provider}", f"model {answer.model}"]
        if answer.usage and answer.usage.total_tokens is not None:
            parts.append(f"{answer.usage.total_tokens} tokens")
        parts.append(f"{answer.latency_ms} ms")
        if answer.failover_used:
            parts.append("failover used")
        return "-# " + " · ".join(parts)

    @staticmethod
    def memory_lines(entries: Sequence[Memory]) -> str:
        """Render stored memories for the member to read."""
        return "\n".join(f"**{entry.key}**: {entry.value}" for entry in entries)
