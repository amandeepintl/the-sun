"""Async engine and session management.

This module owns connection lifecycles and nothing else: every SQL statement in
the project lives in a repository.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from the_sun.config import DatabaseConfig
from the_sun.errors import PersistenceError

__all__ = ["Database", "create_database"]

logger = logging.getLogger(__name__)


class Database:
    """Owns the async engine and hands out sessions."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._engine: AsyncEngine | None = None

    @property
    def config(self) -> DatabaseConfig:
        return self._config

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self._config.url,
                echo=self._config.echo,
                pool_size=self._config.pool_size,
                max_overflow=self._config.max_overflow,
                pool_recycle=self._config.pool_recycle_seconds,
                pool_pre_ping=True,
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, translating driver failures into PersistenceError."""
        session = self.session_factory()
        try:
            yield session
        except (SQLAlchemyError, OSError) as exc:
            # OSError covers drivers that raise connection failures before
            # SQLAlchemy can wrap them; both are persistence failures here.
            await session.rollback()
            raise PersistenceError(str(exc)) from exc
        finally:
            await session.close()

    async def dispose(self) -> None:
        """Close every pooled connection."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None


def create_database(config: DatabaseConfig) -> Database:
    """Build a :class:`Database` from resolved settings."""
    return Database(config)
