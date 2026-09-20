"""Guild settings repository.

Rows are provisioned on first use from a real Discord guild id, and only a
documented allow-list of columns can be updated, so a settings command can never
write an arbitrary field.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.db.models import GuildSettings
from the_sun.errors import InvalidInputError
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows

__all__ = ["UPDATABLE_FIELDS", "GuildSettingsRepository", "SqlAlchemyGuildSettingsRepository"]

#: Columns an administrator may change through the settings command.
UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "ai_provider",
        "ai_model",
        "system_prompt",
        "history_length",
        "memory_enabled",
        "ephemeral_responses",
        "rate_limit_per_user_per_minute",
        "rate_limit_per_guild_per_minute",
        "daily_token_quota",
        "admin_role_ids",
        "allowed_channel_ids",
        "history_retention_days",
    }
)


class GuildSettingsRepository(SqlAlchemyRepository):
    """Persistence operations for per-guild configuration."""

    @abstractmethod
    async def get(self, guild_id: int) -> GuildSettings | None: ...

    @abstractmethod
    async def get_or_create(self, guild_id: int) -> GuildSettings: ...

    @abstractmethod
    async def update(self, guild_id: int, changes: Mapping[str, Any]) -> GuildSettings: ...

    @abstractmethod
    async def delete(self, guild_id: int) -> bool: ...

    @abstractmethod
    async def count(self) -> int: ...


class SqlAlchemyGuildSettingsRepository(GuildSettingsRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, guild_id: int) -> GuildSettings | None:
        return await self._session.get(GuildSettings, guild_id)

    async def get_or_create(self, guild_id: int) -> GuildSettings:
        settings = await self.get(guild_id)
        if settings is None:
            settings = GuildSettings(guild_id=guild_id)
            self._session.add(settings)
            await self._session.flush()
            await self._session.refresh(settings)
        return settings

    async def update(self, guild_id: int, changes: Mapping[str, Any]) -> GuildSettings:
        unknown = set(changes) - UPDATABLE_FIELDS
        if unknown:
            raise InvalidInputError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        settings = await self.get_or_create(guild_id)
        for field, value in changes.items():
            setattr(settings, field, value)
        await self._session.flush()
        return settings

    async def delete(self, guild_id: int) -> bool:
        result = await self._session.execute(
            delete(GuildSettings).where(GuildSettings.guild_id == guild_id)
        )
        return affected_rows(result) > 0

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(GuildSettings))
        return int(total or 0)
