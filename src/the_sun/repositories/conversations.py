"""Conversation repository."""

from __future__ import annotations

import uuid
from abc import abstractmethod
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from the_sun.db.models import Conversation, ConversationScope
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows

__all__ = ["ConversationRepository", "SqlAlchemyConversationRepository"]


def nullable_equals(column: InstrumentedAttribute[Any], value: int | None) -> ColumnElement[bool]:
    """Build ``IS NULL`` or ``= value`` depending on the caller's context."""
    return column.is_(None) if value is None else column == value


class ConversationRepository(SqlAlchemyRepository):
    """Persistence operations for conversation threads."""

    @abstractmethod
    async def get(self, conversation_id: uuid.UUID) -> Conversation | None: ...

    @abstractmethod
    async def create(
        self,
        *,
        scope: ConversationScope,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int | None,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> Conversation: ...

    @abstractmethod
    async def get_active(
        self,
        *,
        scope: ConversationScope,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int | None,
    ) -> Conversation | None: ...

    @abstractmethod
    async def touch(
        self, conversation: Conversation, *, provider: str | None, model: str | None
    ) -> Conversation: ...

    @abstractmethod
    async def deactivate(self, conversation_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def list_recent(self, *, guild_id: int | None, limit: int) -> list[Conversation]: ...

    @abstractmethod
    async def delete_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def count(self) -> int: ...


class SqlAlchemyConversationRepository(ConversationRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self._session.get(Conversation, conversation_id)

    async def create(
        self,
        *,
        scope: ConversationScope,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int | None,
        provider: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> Conversation:
        conversation = Conversation(
            scope=scope,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            provider=provider,
            model=model,
            title=title,
        )
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def get_active(
        self,
        *,
        scope: ConversationScope,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int | None,
    ) -> Conversation | None:
        statement = (
            select(Conversation)
            .where(
                Conversation.scope == scope,
                Conversation.is_active.is_(True),
                nullable_equals(Conversation.guild_id, guild_id),
                nullable_equals(Conversation.channel_id, channel_id),
                nullable_equals(Conversation.user_id, user_id),
            )
            .order_by(Conversation.last_activity_at.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def touch(
        self, conversation: Conversation, *, provider: str | None, model: str | None
    ) -> Conversation:
        conversation.last_activity_at = func.now()
        if provider is not None:
            conversation.provider = provider
        if model is not None:
            conversation.model = model
        await self._session.flush()
        return conversation

    async def deactivate(self, conversation_id: uuid.UUID) -> bool:
        conversation = await self.get(conversation_id)
        if conversation is None:
            return False
        conversation.is_active = False
        await self._session.flush()
        return True

    async def list_recent(self, *, guild_id: int | None, limit: int) -> list[Conversation]:
        statement = select(Conversation).order_by(Conversation.last_activity_at.desc()).limit(limit)
        if guild_id is not None:
            statement = statement.where(Conversation.guild_id == guild_id)
        result = await self._session.scalars(statement)
        return list(result)

    async def delete_for_user(self, user_id: int) -> int:
        result = await self._session.execute(
            delete(Conversation).where(Conversation.user_id == user_id)
        )
        return affected_rows(result)

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(Conversation))
        return int(total or 0)
