"""Repository contracts.

Every aggregate exposes an abstract contract plus a SQLAlchemy implementation.
Services depend on the abstract type, so the persistence strategy is a detail of
this layer, and no SQL statement escapes it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["Repository", "SqlAlchemyRepository", "affected_rows"]


def affected_rows(result: Result[Any]) -> int:
    """Rows changed by a DML statement, or zero for statement types without one."""
    if isinstance(result, CursorResult):
        return int(result.rowcount or 0)
    return 0


class Repository:
    """Marker base class for every repository contract."""


class SqlAlchemyRepository(Repository, ABC):
    """Base implementation holding the session supplied by a UnitOfWork."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    @abstractmethod
    async def count(self) -> int:
        """Total number of rows in the aggregate."""
