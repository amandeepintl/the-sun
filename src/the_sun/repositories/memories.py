"""Memory repository.

Entries belong to one user and may optionally be scoped to one guild. Because
global rows store ``NULL`` for ``guild_id``, the lookup here also uses
``IS NULL`` rather than an equality comparison.
"""

from __future__ import annotations

from abc import abstractmethod

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.db.models import Memory, MemorySource
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows

__all__ = ["MemoryRepository", "SqlAlchemyMemoryRepository"]


class MemoryRepository(SqlAlchemyRepository):
    """Persistence operations for explicit user memories."""

    @abstractmethod
    async def upsert(
        self,
        *,
        user_id: int,
        key: str,
        value: str,
        guild_id: int | None = None,
        source: MemorySource = MemorySource.EXPLICIT,
    ) -> Memory: ...

    @abstractmethod
    async def get(self, *, user_id: int, key: str, guild_id: int | None) -> Memory | None: ...

    @abstractmethod
    async def list_for_user(
        self, user_id: int, *, guild_id: int | None = None, include_global: bool = True
    ) -> list[Memory]: ...

    @abstractmethod
    async def delete(self, *, user_id: int, key: str, guild_id: int | None) -> bool: ...

    @abstractmethod
    async def count_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def delete_all_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def count(self) -> int: ...


class SqlAlchemyMemoryRepository(MemoryRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert(
        self,
        *,
        user_id: int,
        key: str,
        value: str,
        guild_id: int | None = None,
        source: MemorySource = MemorySource.EXPLICIT,
    ) -> Memory:
        existing = await self.get(user_id=user_id, key=key, guild_id=guild_id)
        if existing is None:
            existing = Memory(
                user_id=user_id, guild_id=guild_id, key=key, value=value, source=source
            )
            self._session.add(existing)
        else:
            existing.value = value
            existing.source = source
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def get(self, *, user_id: int, key: str, guild_id: int | None) -> Memory | None:
        statement = select(Memory).where(
            Memory.user_id == user_id,
            Memory.key == key,
            Memory.guild_id.is_(None) if guild_id is None else Memory.guild_id == guild_id,
        )
        return await self._session.scalar(statement)

    async def list_for_user(
        self, user_id: int, *, guild_id: int | None = None, include_global: bool = True
    ) -> list[Memory]:
        """List the memories visible to a user in a given context.

        With ``guild_id=None`` (for example a direct message) only global entries
        are returned. Inside a guild the guild-scoped entries are returned, plus
        the global ones when ``include_global`` is set.
        """
        statement = select(Memory).where(Memory.user_id == user_id)
        if guild_id is None:
            if not include_global:
                return []
            statement = statement.where(Memory.guild_id.is_(None))
        else:
            scope_filters: list[ColumnElement[bool]] = [Memory.guild_id == guild_id]
            if include_global:
                scope_filters.append(Memory.guild_id.is_(None))
            statement = statement.where(or_(*scope_filters))
        statement = statement.order_by(Memory.key)
        return list(await self._session.scalars(statement))

    async def delete(self, *, user_id: int, key: str, guild_id: int | None) -> bool:
        statement = delete(Memory).where(
            Memory.user_id == user_id,
            Memory.key == key,
            Memory.guild_id.is_(None) if guild_id is None else Memory.guild_id == guild_id,
        )
        result = await self._session.execute(statement)
        return affected_rows(result) > 0

    async def count_for_user(self, user_id: int) -> int:
        """Every saved entry for one member, in any scope."""
        total = await self._session.scalar(
            select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
        )
        return int(total or 0)

    async def delete_all_for_user(self, user_id: int) -> int:
        result = await self._session.execute(delete(Memory).where(Memory.user_id == user_id))
        return affected_rows(result)

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(Memory))
        return int(total or 0)
