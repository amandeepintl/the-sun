"""Usage service.

Every command invocation is recorded, and the statistics command reads only these
records. Token counts are whatever the provider reported, latency is measured
locally, and failures are recorded with their error code - nothing is estimated
or backfilled with plausible-looking numbers.

Recording is best effort: a telemetry write must never break the user's request,
so database failures are logged and swallowed here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from the_sun.db.models import UsageEvent, UsageStatus, UsageSurface
from the_sun.errors import InvalidInputError, PersistenceError
from the_sun.repositories import CommandUsage, DailyUsage, Page, ProviderUsage, UsageSummary
from the_sun.services.base import ServiceContext

__all__ = ["UsageRecord", "UsageService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One invocation to persist."""

    command: str
    surface: UsageSurface = UsageSurface.SLASH_COMMAND
    guild_id: int | None = None
    channel_id: int | None = None
    user_id: int | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    status: UsageStatus = UsageStatus.OK
    error_code: str | None = None
    correlation_id: str | None = None


class UsageService:
    """Writes usage events and computes statistics from them."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    def window_start(self, *, days: int | None = None, hours: int | None = None) -> datetime | None:
        """Start of a rolling window measured from the injected clock."""
        if days is None and hours is None:
            return None
        delta = timedelta(days=days or 0, hours=hours or 0)
        return self._context.clock() - delta

    async def record(self, record: UsageRecord) -> None:
        """Persist one invocation, never failing the caller."""
        await self.record_many([record])

    async def record_many(self, records: Iterable[UsageRecord]) -> None:
        payload = [asdict(record) for record in records]
        if not payload:
            return
        try:
            async with self._context.unit_of_work() as uow:
                await uow.usage.append_many(payload)
        except PersistenceError as exc:
            logger.warning(
                "usage event could not be recorded", extra={"error": str(exc)}, exc_info=exc
            )

    async def summary(
        self,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        since: datetime | None = None,
    ) -> UsageSummary:
        async with self._context.unit_of_work() as uow:
            return await uow.usage.summary(guild_id=guild_id, user_id=user_id, since=since)

    def day_start(self) -> datetime:
        """Midnight UTC of the current day, from the injected clock."""
        now = self._context.clock()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def tokens_today(self, guild_id: int) -> int:
        """Tokens this guild has used since midnight UTC today.

        The sum comes from recorded provider usage only: requests whose provider
        reported no usage contribute nothing rather than an estimate.
        """
        if guild_id <= 0:
            raise InvalidInputError("a guild id must be a positive Discord snowflake")
        summary = await self.summary(guild_id=guild_id, since=self.day_start())
        return summary.prompt_tokens + summary.completion_tokens

    async def top_commands(
        self, *, guild_id: int | None = None, since: datetime | None = None, limit: int = 10
    ) -> list[CommandUsage]:
        async with self._context.unit_of_work() as uow:
            return await uow.usage.top_commands(guild_id=guild_id, since=since, limit=limit)

    async def provider_breakdown(
        self, *, guild_id: int | None = None, since: datetime | None = None, limit: int = 10
    ) -> list[ProviderUsage]:
        async with self._context.unit_of_work() as uow:
            return await uow.usage.provider_breakdown(guild_id=guild_id, since=since, limit=limit)

    async def daily_series(
        self, *, guild_id: int | None = None, since: datetime | None = None, limit: int = 30
    ) -> list[DailyUsage]:
        async with self._context.unit_of_work() as uow:
            return await uow.usage.daily_series(guild_id=guild_id, since=since, limit=limit)

    async def events_page(
        self, *, guild_id: int | None = None, limit: int = 25, cursor: str | None = None
    ) -> Page[UsageEvent]:
        async with self._context.unit_of_work() as uow:
            return await uow.usage.events_page(guild_id=guild_id, limit=limit, cursor=cursor)
