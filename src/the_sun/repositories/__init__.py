"""Repository layer.

Services depend on the abstract repository types exported here, so no SQL
statement and no session detail leaks out of this package.
"""

from __future__ import annotations

from collections.abc import Callable

from the_sun.db import Database
from the_sun.repositories.base import Repository, SqlAlchemyRepository
from the_sun.repositories.conversations import (
    ConversationRepository,
    SqlAlchemyConversationRepository,
    nullable_equals,
)
from the_sun.repositories.database_info import (
    DatabaseInfo,
    DatabaseInfoRepository,
    SqlAlchemyDatabaseInfoRepository,
)
from the_sun.repositories.guild_settings import (
    UPDATABLE_FIELDS,
    GuildSettingsRepository,
    SqlAlchemyGuildSettingsRepository,
)
from the_sun.repositories.memories import MemoryRepository, SqlAlchemyMemoryRepository
from the_sun.repositories.messages import MessageRepository, SqlAlchemyMessageRepository
from the_sun.repositories.pagination import Cursor, Page, decode_cursor, encode_cursor
from the_sun.repositories.uow import UnitOfWork
from the_sun.repositories.usage import (
    CommandUsage,
    DailyUsage,
    ProviderUsage,
    SqlAlchemyUsageRepository,
    UsageRepository,
    UsageSummary,
)
from the_sun.repositories.users import SqlAlchemyUserRepository, UserRepository

__all__ = [
    "UPDATABLE_FIELDS",
    "CommandUsage",
    "ConversationRepository",
    "Cursor",
    "DailyUsage",
    "DatabaseInfo",
    "DatabaseInfoRepository",
    "GuildSettingsRepository",
    "MemoryRepository",
    "MessageRepository",
    "Page",
    "ProviderUsage",
    "Repository",
    "SqlAlchemyConversationRepository",
    "SqlAlchemyDatabaseInfoRepository",
    "SqlAlchemyGuildSettingsRepository",
    "SqlAlchemyMemoryRepository",
    "SqlAlchemyMessageRepository",
    "SqlAlchemyRepository",
    "SqlAlchemyUsageRepository",
    "SqlAlchemyUserRepository",
    "UnitOfWork",
    "UsageRepository",
    "UsageSummary",
    "UserRepository",
    "build_unit_of_work_factory",
    "decode_cursor",
    "encode_cursor",
    "nullable_equals",
]


def build_unit_of_work_factory(database: Database) -> Callable[[], UnitOfWork]:
    """Return a factory handing out units of work bound to ``database``."""

    def factory() -> UnitOfWork:
        return UnitOfWork(database.session_factory)

    return factory
