"""Database introspection repository.

Health checks and migration verification need to ask the database about itself.
Those queries live here rather than in the session layer or in a service, so the
"all SQL lives in repositories" invariant stays true.
"""

from __future__ import annotations

import time
from abc import abstractmethod
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.repositories.base import SqlAlchemyRepository

__all__ = ["DatabaseInfo", "DatabaseInfoRepository", "SqlAlchemyDatabaseInfoRepository"]


@dataclass(frozen=True, slots=True)
class DatabaseInfo:
    """Facts read back from a live PostgreSQL connection."""

    server_version: str
    migration_revision: str | None
    tables: list[str]
    latency_ms: float


class DatabaseInfoRepository(SqlAlchemyRepository):
    """Read-only introspection."""

    @abstractmethod
    async def server_version(self) -> str: ...

    @abstractmethod
    async def applied_migration_revision(self) -> str | None: ...

    @abstractmethod
    async def table_names(self) -> list[str]: ...

    @abstractmethod
    async def describe(self) -> DatabaseInfo: ...


class SqlAlchemyDatabaseInfoRepository(DatabaseInfoRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def server_version(self) -> str:
        version = await self._session.scalar(text("SHOW server_version"))
        return str(version)

    async def applied_migration_revision(self) -> str | None:
        exists = await self._session.scalar(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_schema = current_schema() AND table_name = 'alembic_version'"
                ")"
            )
        )
        if not exists:
            return None
        revision = await self._session.scalar(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        return str(revision) if revision is not None else None

    async def table_names(self) -> list[str]:
        rows = await self._session.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
                " ORDER BY table_name"
            )
        )
        return [str(row[0]) for row in rows]

    async def describe(self) -> DatabaseInfo:
        started = time.perf_counter()
        version = await self.server_version()
        revision = await self.applied_migration_revision()
        tables = await self.table_names()
        return DatabaseInfo(
            server_version=version,
            migration_revision=revision,
            tables=tables,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def count(self) -> int:
        total = await self._session.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = current_schema()"
            )
        )
        return int(total or 0)
