"""Shared plumbing for the service layer.

A :class:`ServiceContext` is created once per process and passed to every
service. It owns the objects with real lifecycles - the database engine, the
Redis client and the provider registry - so they are created and closed exactly
once.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from the_sun.ai import ProviderRegistry, create_registry
from the_sun.cache import CacheBackend, CacheKeys, create_cache_backend
from the_sun.config import Settings
from the_sun.db import Database, create_database
from the_sun.repositories import UnitOfWork, build_unit_of_work_factory
from the_sun.services.ai_gateway import AIGateway, build_gateway

__all__ = ["ServiceContext", "build_service_context"]

Clock = Callable[[], datetime]


class ServiceContext:
    """Everything a service needs, wired from resolved settings."""

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        cache: CacheBackend | None = None,
        providers: ProviderRegistry | None = None,
        clock: Clock | None = None,
        uow_factory: Callable[[], UnitOfWork] | None = None,
    ) -> None:
        self.settings = settings
        self.database: Database = database or create_database(settings.database)
        self.cache: CacheBackend = cache or create_cache_backend(settings.cache)
        self.providers: ProviderRegistry = providers or create_registry(settings.ai)
        self.clock: Clock = clock or (lambda: datetime.now(tz=UTC))
        # The factory is injectable so a caller can supply its own transaction
        # scope (integration tests join their rollback-only transaction).
        self._uow_factory = uow_factory or build_unit_of_work_factory(self.database)
        self.ai: AIGateway = build_gateway(
            self.providers,
            cache=self.cache,
            keys=self.keys,
            ai_config=settings.ai,
            clock=self.clock,
        )

    @property
    def keys(self) -> CacheKeys:
        """Key builder for the cache actually in use (including test prefixes)."""
        return CacheKeys(prefix=self.cache.key_prefix)

    @property
    def default_provider_name(self) -> str:
        return self.providers.default_name

    def unit_of_work(self) -> UnitOfWork:
        """Open a new unit of work. Use as ``async with context.unit_of_work() as uow``."""
        return self._uow_factory()

    async def aclose(self) -> None:
        """Release every owned resource."""
        await self.providers.aclose()
        await self.cache.close()
        await self.database.dispose()


def build_service_context(settings: Settings) -> ServiceContext:
    """Create the process-wide service context from settings."""
    return ServiceContext(settings)
