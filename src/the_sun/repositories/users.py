"""User repository."""

from __future__ import annotations

from abc import abstractmethod

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.db.models import User
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows

__all__ = ["SqlAlchemyUserRepository", "UserRepository"]


class UserRepository(SqlAlchemyRepository):
    """Persistence operations for Discord users."""

    @abstractmethod
    async def get(self, user_id: int) -> User | None: ...

    @abstractmethod
    async def upsert_seen(self, user_id: int, display_name: str | None) -> User: ...

    @abstractmethod
    async def delete(self, user_id: int) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def upsert_seen(self, user_id: int, display_name: str | None) -> User:
        """Record that a user interacted, refreshing their observed name."""
        user = await self.get(user_id)
        if user is None:
            user = User(user_id=user_id, display_name=display_name)
            self._session.add(user)
        elif display_name and user.display_name != display_name:
            user.display_name = display_name
        await self._session.flush()
        return user

    async def delete(self, user_id: int) -> bool:
        result = await self._session.execute(delete(User).where(User.user_id == user_id))
        return affected_rows(result) > 0

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(User))
        return int(total or 0)
