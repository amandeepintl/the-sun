"""Guild settings service.

Reads are cache-aside through Redis and writes invalidate the cached entry, so a
guild that has never used the bot is provisioned from its real Discord id on
first use and every server configures itself independently.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from the_sun.cache import DEFAULT_TTLS, CacheNamespace, get_or_load, invalidate
from the_sun.db.models import GuildSettings
from the_sun.errors import ConfigurationError
from the_sun.services.base import ServiceContext

__all__ = ["GuildSettingsView", "SettingsService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuildSettingsView:
    """Serialisable snapshot of a guild's configuration.

    ``guild_id`` is ``None`` for direct messages, which have no stored row and
    therefore use the documented DM defaults.
    """

    guild_id: int | None
    ai_provider: str | None
    ai_model: str | None
    system_prompt: str | None
    history_length: int
    memory_enabled: bool
    ephemeral_responses: bool
    rate_limit_per_user_per_minute: int | None
    rate_limit_per_guild_per_minute: int | None
    daily_token_quota: int | None
    admin_role_ids: list[int]
    allowed_channel_ids: list[int]
    history_retention_days: int | None
    updated_at: datetime

    @classmethod
    def for_direct_message(cls, *, history_length: int, updated_at: datetime) -> GuildSettingsView:
        """Defaults for a DM: private replies, memory allowed, no guild overrides."""
        return cls(
            guild_id=None,
            ai_provider=None,
            ai_model=None,
            system_prompt=None,
            history_length=history_length,
            memory_enabled=True,
            ephemeral_responses=True,
            rate_limit_per_user_per_minute=None,
            rate_limit_per_guild_per_minute=None,
            daily_token_quota=None,
            admin_role_ids=[],
            allowed_channel_ids=[],
            history_retention_days=None,
            updated_at=updated_at,
        )

    @classmethod
    def from_model(cls, model: GuildSettings) -> GuildSettingsView:
        return cls(
            guild_id=model.guild_id,
            ai_provider=model.ai_provider,
            ai_model=model.ai_model,
            system_prompt=model.system_prompt,
            history_length=model.history_length,
            memory_enabled=model.memory_enabled,
            ephemeral_responses=model.ephemeral_responses,
            rate_limit_per_user_per_minute=model.rate_limit_per_user_per_minute,
            rate_limit_per_guild_per_minute=model.rate_limit_per_guild_per_minute,
            daily_token_quota=model.daily_token_quota,
            admin_role_ids=list(model.admin_role_ids or []),
            allowed_channel_ids=list(model.allowed_channel_ids or []),
            history_retention_days=model.history_retention_days,
            updated_at=model.updated_at,
        )

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["updated_at"] = self.updated_at.isoformat()
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> GuildSettingsView:
        data = dict(payload)
        data["updated_at"] = datetime.fromisoformat(str(data["updated_at"]))
        return cls(**data)

    def is_channel_allowed(self, channel_id: int | None) -> bool:
        """Empty allow-list means every channel is allowed."""
        if not self.allowed_channel_ids:
            return True
        return channel_id is not None and channel_id in self.allowed_channel_ids


class SettingsService:
    """Reads and writes guild configuration."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    async def get(self, guild_id: int) -> GuildSettingsView:
        """Return a guild's settings, provisioning and caching them as needed."""
        if guild_id <= 0:
            raise ConfigurationError("a guild id must be a positive Discord snowflake")
        key = self._context.keys.settings(guild_id)

        async def load_from_database() -> GuildSettingsView:
            async with self._context.unit_of_work() as uow:
                model = await uow.guild_settings.get_or_create(guild_id)
                return GuildSettingsView.from_model(model)

        return await get_or_load(
            self._context.cache,
            key=key,
            ttl_seconds=DEFAULT_TTLS[CacheNamespace.SETTINGS],
            loader=load_from_database,
            dump=lambda view: view.to_payload(),
            load=GuildSettingsView.from_payload,
        )

    async def update(self, guild_id: int, changes: Mapping[str, Any]) -> GuildSettingsView:
        """Apply validated changes and invalidate the cached copy."""
        async with self._context.unit_of_work() as uow:
            model = await uow.guild_settings.update(guild_id, changes)
            view = GuildSettingsView.from_model(model)
        await self.invalidate(guild_id)
        logger.info(
            "guild settings updated",
            extra={"guild_id": guild_id, "fields": sorted(changes)},
        )
        return view

    async def invalidate(self, guild_id: int) -> int:
        """Drop the cached settings for one guild."""
        return await invalidate(self._context.cache, self._context.keys.settings(guild_id))

    def provider_for(self, view: GuildSettingsView) -> str:
        """Provider name a guild should use, falling back to the default."""
        return view.ai_provider or self._context.default_provider_name

    def direct_message_defaults(self) -> GuildSettingsView:
        """Settings used when an interaction has no guild."""
        return GuildSettingsView.for_direct_message(
            history_length=self._context.settings.default_history_length,
            updated_at=self._context.clock(),
        )
