"""Message repository."""

from __future__ import annotations

import uuid
from abc import abstractmethod
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.db.models import Conversation, GuildSettings, Message, MessageKind, MessageRole
from the_sun.errors import InvalidInputError
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows
from the_sun.repositories.pagination import Page, decode_cursor, encode_cursor

__all__ = ["MessageRepository", "SqlAlchemyMessageRepository"]


class MessageRepository(SqlAlchemyRepository):
    """Persistence operations for conversation turns."""

    @abstractmethod
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
    ) -> Message: ...

    @abstractmethod
    async def get(self, message_id: uuid.UUID) -> Message | None: ...

    @abstractmethod
    async def recent_window(self, conversation_id: uuid.UUID, *, limit: int) -> list[Message]: ...

    @abstractmethod
    async def history_page(
        self, conversation_id: uuid.UUID, *, limit: int, cursor: str | None = None
    ) -> Page[Message]: ...

    @abstractmethod
    async def count_for_conversation(self, conversation_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def count_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def for_user(self, user_id: int, *, limit: int) -> list[Message]: ...

    @abstractmethod
    async def delete_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def purge_unretained(
        self, *, now: datetime, default_retention_days: int | None
    ) -> int: ...

    @abstractmethod
    async def purge_older_than(self, cutoff: datetime) -> int: ...

    @abstractmethod
    async def count(self) -> int: ...


class SqlAlchemyMessageRepository(MessageRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

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
        message = Message(
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
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def get(self, message_id: uuid.UUID) -> Message | None:
        return await self._session.get(Message, message_id)

    async def recent_window(self, conversation_id: uuid.UUID, *, limit: int) -> list[Message]:
        """Return the newest ``limit`` turns in chronological order."""
        if limit <= 0:
            return []
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        newest_first = list(await self._session.scalars(statement))
        newest_first.reverse()
        return newest_first

    async def history_page(
        self, conversation_id: uuid.UUID, *, limit: int, cursor: str | None = None
    ) -> Page[Message]:
        """Newest-first paginated history for the history command."""
        statement = select(Message).where(Message.conversation_id == conversation_id)
        if cursor:
            position = decode_cursor(cursor)
            try:
                identifier = uuid.UUID(position.identifier)
            except ValueError as exc:
                raise InvalidInputError(
                    "the pagination cursor is not valid"
                ) from exc  # Keyset predicate spelled out rather than as a row comparison, so the
            # ordering stays correct when several rows share a timestamp.
            statement = statement.where(
                or_(
                    Message.created_at < position.created_at,
                    and_(
                        Message.created_at == position.created_at,
                        Message.id < identifier,
                    ),
                )
            )
        statement = statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(
            limit + 1
        )
        rows = list(await self._session.scalars(statement))
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
        )
        return Page(items=items, next_cursor=next_cursor, limit=limit)

    async def count_for_conversation(self, conversation_id: uuid.UUID) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return int(total or 0)

    async def count_for_user(self, user_id: int) -> int:
        """Turns stored in any conversation belonging to one member."""
        total = await self._session.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id)
        )
        return int(total or 0)

    async def for_user(self, user_id: int, *, limit: int) -> list[Message]:
        """Newest-first turns across every conversation one member took part in."""
        if limit <= 0:
            return []
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.user_id == user_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        return list(await self._session.scalars(statement))

    async def delete_for_user(self, user_id: int) -> int:
        conversation_ids = select(Conversation.id).where(Conversation.user_id == user_id)
        result = await self._session.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        )
        return affected_rows(result)

    async def purge_unretained(self, *, now: datetime, default_retention_days: int | None) -> int:
        """Delete turns whose configured retention window has elapsed.

        Each conversation follows its guild's window; a conversation with no guild
        (a direct message) follows ``default_retention_days``. ``make_interval``
        returns ``NULL`` when neither is set, and a ``NULL`` comparison never
        matches, so history is only ever deleted because someone asked for it.
        """
        guild_window = (
            select(GuildSettings.history_retention_days)
            .join(Conversation, Conversation.guild_id == GuildSettings.guild_id)
            .where(Conversation.id == Message.conversation_id)
            .scalar_subquery()
        )
        window = func.coalesce(guild_window, default_retention_days)
        cutoff = now - func.make_interval(days=window)
        result = await self._session.execute(delete(Message).where(Message.created_at < cutoff))
        return affected_rows(result)

    async def purge_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(delete(Message).where(Message.created_at < cutoff))
        return affected_rows(result)

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(Message))
        return int(total or 0)
