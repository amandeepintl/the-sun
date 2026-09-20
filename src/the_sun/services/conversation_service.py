"""Conversation service.

Conversations are per member per channel inside a guild, and per member in direct
messages, so two people asking in the same channel never see each other's thread.
Every stored turn keeps the Discord coordinates it came from, which is what lets
``/history`` and ``/privacy`` show and delete exactly what was recorded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from the_sun.db.models import Conversation, ConversationScope, Message, MessageKind, MessageRole
from the_sun.repositories import Page
from the_sun.services.base import ServiceContext

__all__ = ["ConversationService", "ConversationTurn"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """A stored turn, as shown to a member by ``/history``."""

    role: MessageRole
    kind: MessageKind
    content: str
    created_at: datetime
    provider: str | None
    model: str | None
    discord_message_id: int | None

    @classmethod
    def from_model(cls, model: Message) -> ConversationTurn:
        return cls(
            role=model.role,
            kind=model.kind,
            content=model.content,
            created_at=model.created_at,
            provider=model.provider,
            model=model.model,
            discord_message_id=model.discord_message_id,
        )


class ConversationService:
    """Resolves conversations and persists their turns."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    @staticmethod
    def scope_for(guild_id: int | None) -> ConversationScope:
        """Direct messages are private to the member; guilds thread per channel."""
        return ConversationScope.USER if guild_id is None else ConversationScope.USER_CHANNEL

    async def current(
        self,
        *,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int,
        provider: str | None = None,
        model: str | None = None,
    ) -> Conversation:
        """The active conversation for this member, created on first use."""
        scope = self.scope_for(guild_id)
        async with self._context.unit_of_work() as uow:
            conversation = await uow.conversations.get_active(
                scope=scope,
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
            )
            if conversation is None:
                conversation = await uow.conversations.create(
                    scope=scope,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    provider=provider,
                    model=model,
                )
            else:
                conversation = await uow.conversations.touch(
                    conversation, provider=provider, model=model
                )
            return conversation

    async def find_active(
        self, *, guild_id: int | None, channel_id: int | None, user_id: int
    ) -> Conversation | None:
        """The active conversation, without creating one."""
        async with self._context.unit_of_work() as uow:
            return await uow.conversations.get_active(
                scope=self.scope_for(guild_id),
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
            )

    async def count_for_user(self, user_id: int) -> int:
        """Stored turns across every conversation one member took part in."""
        async with self._context.unit_of_work() as uow:
            return await uow.messages.count_for_user(user_id)

    async def turns_for_user(self, user_id: int, *, limit: int) -> list[Message]:
        """Newest-first turns across one member's conversations."""
        async with self._context.unit_of_work() as uow:
            return await uow.messages.for_user(user_id, limit=limit)

    async def append(
        self,
        *,
        conversation_id: uuid.UUID,
        role: MessageRole,
        kind: MessageKind,
        content: str,
        discord_guild_id: int | None = None,
        discord_channel_id: int | None = None,
        discord_message_id: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
        correlation_id: str | None = None,
    ) -> Message:
        """Store one turn of the conversation."""
        async with self._context.unit_of_work() as uow:
            return await uow.messages.append(
                conversation_id=conversation_id,
                role=role,
                kind=kind,
                content=content,
                discord_guild_id=discord_guild_id,
                discord_channel_id=discord_channel_id,
                discord_message_id=discord_message_id,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                correlation_id=correlation_id,
            )

    async def recent_window(self, conversation_id: uuid.UUID, *, limit: int) -> list[Message]:
        """The newest ``limit`` turns, oldest first, for the next prompt."""
        if limit <= 0:
            return []
        async with self._context.unit_of_work() as uow:
            return await uow.messages.recent_window(conversation_id, limit=limit)

    async def history_page(
        self, conversation_id: uuid.UUID, *, limit: int, cursor: str | None = None
    ) -> Page[Message]:
        """Newest-first page of stored turns, for ``/history``."""
        async with self._context.unit_of_work() as uow:
            return await uow.messages.history_page(conversation_id, limit=limit, cursor=cursor)

    async def count(self, conversation_id: uuid.UUID) -> int:
        async with self._context.unit_of_work() as uow:
            return await uow.messages.count_for_conversation(conversation_id)

    async def reset(self, *, guild_id: int | None, channel_id: int | None, user_id: int) -> bool:
        """Close the active conversation so the next question starts fresh."""
        scope = self.scope_for(guild_id)
        async with self._context.unit_of_work() as uow:
            conversation = await uow.conversations.get_active(
                scope=scope,
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
            )
            if conversation is None:
                return False
            await uow.conversations.deactivate(conversation.id)
            return True

    async def delete_for_user(self, user_id: int) -> tuple[int, int]:
        """Delete every conversation and turn belonging to one member."""
        async with self._context.unit_of_work() as uow:
            turns = await uow.messages.delete_for_user(user_id)
            conversations = await uow.conversations.delete_for_user(user_id)
            return conversations, turns
