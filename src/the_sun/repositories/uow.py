"""Unit of work.

A unit of work owns exactly one session and one transaction. Services open a
unit of work per interaction, so a failure anywhere inside the block leaves the
database untouched. When constructed with an existing session (as integration
tests do) it joins that session's transaction through a SAVEPOINT instead of
committing.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from the_sun.errors import PersistenceError
from the_sun.repositories.base import SqlAlchemyRepository
from the_sun.repositories.conversations import (
    ConversationRepository,
    SqlAlchemyConversationRepository,
)
from the_sun.repositories.database_info import (
    DatabaseInfoRepository,
    SqlAlchemyDatabaseInfoRepository,
)
from the_sun.repositories.guild_settings import (
    GuildSettingsRepository,
    SqlAlchemyGuildSettingsRepository,
)
from the_sun.repositories.memories import MemoryRepository, SqlAlchemyMemoryRepository
from the_sun.repositories.messages import MessageRepository, SqlAlchemyMessageRepository
from the_sun.repositories.usage import SqlAlchemyUsageRepository, UsageRepository
from the_sun.repositories.users import SqlAlchemyUserRepository, UserRepository

__all__ = ["UnitOfWork"]


class UnitOfWork:
    """Transaction scope plus lazily constructed repositories."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        session: AsyncSession | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._session = session
        self._owns_session = session is None
        self._transaction: object | None = None
        self._repositories: dict[type[Any], Any] = {}

    @property
    def session(self) -> AsyncSession:
        if self._session is None:  # pragma: no cover - guarded by __aenter__
            raise RuntimeError("the unit of work has not been entered")
        return self._session

    @property
    def owns_session(self) -> bool:
        return self._owns_session

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = self._session_factory()
        if self._owns_session:
            self._transaction = await self._session.begin()
        else:
            # Joining a caller-managed transaction (integration tests).
            self._transaction = await self._session.begin_nested()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit on success, roll back on failure, and type every driver error.

        Any database failure - whether it happened inside the block or while
        committing - leaves the transaction through this method, so callers only
        ever handle :class:`PersistenceError`. The original exception is preserved
        as the cause for logging.
        """
        session = self.session
        transaction = self._transaction
        self._transaction = None
        failure: PersistenceError | None = None
        try:
            if transaction is not None:
                if exc_type is None:
                    try:
                        await transaction.commit()  # type: ignore[attr-defined]
                    except (SQLAlchemyError, OSError) as error:
                        await transaction.rollback()  # type: ignore[attr-defined]
                        failure = PersistenceError(str(error))
                else:
                    await transaction.rollback()  # type: ignore[attr-defined]
                    # OSError covers drivers that surface connection failures
                    # before SQLAlchemy can wrap them (asyncpg, for example).
                    if issubclass(exc_type, (SQLAlchemyError, OSError)):
                        failure = PersistenceError(str(exc))
        finally:
            if self._owns_session:
                await session.close()
                self._session = None
        if failure is not None:
            raise failure from exc

    async def commit(self) -> None:
        """Commit the current transaction explicitly."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Roll back the current transaction explicitly."""
        await self.session.rollback()

    # ------------------------------------------------------------------ #
    # Repositories
    # ------------------------------------------------------------------ #
    @property
    def database_info(self) -> DatabaseInfoRepository:
        return self._repository(SqlAlchemyDatabaseInfoRepository)

    @property
    def users(self) -> UserRepository:
        return self._repository(SqlAlchemyUserRepository)

    @property
    def guild_settings(self) -> GuildSettingsRepository:
        return self._repository(SqlAlchemyGuildSettingsRepository)

    @property
    def conversations(self) -> ConversationRepository:
        return self._repository(SqlAlchemyConversationRepository)

    @property
    def messages(self) -> MessageRepository:
        return self._repository(SqlAlchemyMessageRepository)

    @property
    def memories(self) -> MemoryRepository:
        return self._repository(SqlAlchemyMemoryRepository)

    @property
    def usage(self) -> UsageRepository:
        return self._repository(SqlAlchemyUsageRepository)

    def _repository[RepositoryT: SqlAlchemyRepository](
        self, implementation: type[RepositoryT]
    ) -> RepositoryT:
        cached = self._repositories.get(implementation)
        if cached is None:
            cached = implementation(self.session)
            self._repositories[implementation] = cached
        return cached
