"""Usage repository.

This is the only place statistics are computed. Every aggregate is a SQL
aggregation over real rows: token counts that came from a provider's usage
report, latency measured locally, and statuses recorded from actual outcomes.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.db.models import UsageEvent, UsageStatus, UsageSurface
from the_sun.errors import InvalidInputError
from the_sun.repositories.base import SqlAlchemyRepository, affected_rows
from the_sun.repositories.pagination import Page, decode_cursor, encode_cursor

__all__ = [
    "CommandUsage",
    "DailyUsage",
    "ProviderUsage",
    "SqlAlchemyUsageRepository",
    "UsageRepository",
    "UsageSummary",
]


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Aggregated outcome of a set of invocations."""

    invocations: int
    successful: int
    failed: int
    rate_limited: int
    prompt_tokens: int
    completion_tokens: int
    average_latency_ms: float | None
    first_occurred_at: datetime | None
    last_occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class CommandUsage:
    """Invocation count for one command name."""

    command: str
    invocations: int
    failures: int


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Invocation and token counts for one provider/model pair."""

    provider: str | None
    model: str | None
    invocations: int
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True, slots=True)
class DailyUsage:
    """Per-day totals used by the statistics command."""

    day: datetime
    invocations: int
    prompt_tokens: int
    completion_tokens: int


class UsageRepository(SqlAlchemyRepository):
    """Persistence and aggregation for usage events."""

    @abstractmethod
    async def append(
        self,
        *,
        command: str,
        surface: UsageSurface,
        guild_id: int | None = None,
        channel_id: int | None = None,
        user_id: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
        status: UsageStatus = UsageStatus.OK,
        error_code: str | None = None,
        correlation_id: str | None = None,
    ) -> UsageEvent: ...

    @abstractmethod
    async def append_many(self, events: Iterable[dict[str, Any]]) -> int: ...

    @abstractmethod
    async def summary(
        self,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary: ...

    @abstractmethod
    async def top_commands(
        self,
        *,
        guild_id: int | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[CommandUsage]: ...

    @abstractmethod
    async def provider_breakdown(
        self,
        *,
        guild_id: int | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[ProviderUsage]: ...

    @abstractmethod
    async def daily_series(
        self, *, guild_id: int | None = None, since: datetime | None = None, limit: int = 30
    ) -> list[DailyUsage]: ...

    @abstractmethod
    async def events_page(
        self,
        *,
        guild_id: int | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> Page[UsageEvent]: ...

    @abstractmethod
    async def purge_older_than(self, cutoff: datetime) -> int: ...

    @abstractmethod
    async def purge_for_user(self, user_id: int) -> int: ...

    @abstractmethod
    async def count(self) -> int: ...


def _time_filters(*, since: datetime | None, until: datetime | None) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if since is not None:
        filters.append(UsageEvent.occurred_at >= since)
    if until is not None:
        filters.append(UsageEvent.occurred_at <= until)
    return filters


class SqlAlchemyUsageRepository(UsageRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def append(
        self,
        *,
        command: str,
        surface: UsageSurface,
        guild_id: int | None = None,
        channel_id: int | None = None,
        user_id: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
        status: UsageStatus = UsageStatus.OK,
        error_code: str | None = None,
        correlation_id: str | None = None,
    ) -> UsageEvent:
        event = UsageEvent(
            command=command,
            surface=surface,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            correlation_id=correlation_id,
        )
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def append_many(self, events: Iterable[dict[str, Any]]) -> int:
        payload: Sequence[dict[str, Any]] = list(events)
        if not payload:
            return 0
        await self._session.execute(insert(UsageEvent), payload)
        return len(payload)

    async def summary(
        self,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        statement = select(
            func.count().label("invocations"),
            func.count().filter(UsageEvent.status == UsageStatus.OK).label("successful"),
            func.count().filter(UsageEvent.status == UsageStatus.ERROR).label("failed"),
            func.count()
            .filter(UsageEvent.status == UsageStatus.RATE_LIMITED)
            .label("rate_limited"),
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0).label("completion_tokens"),
            func.avg(UsageEvent.latency_ms).label("average_latency_ms"),
            func.min(UsageEvent.occurred_at).label("first_occurred_at"),
            func.max(UsageEvent.occurred_at).label("last_occurred_at"),
        ).select_from(UsageEvent)
        if guild_id is not None:
            statement = statement.where(UsageEvent.guild_id == guild_id)
        if user_id is not None:
            statement = statement.where(UsageEvent.user_id == user_id)
        for condition in _time_filters(since=since, until=until):
            statement = statement.where(condition)
        row = (await self._session.execute(statement)).one()
        return UsageSummary(
            invocations=int(row.invocations or 0),
            successful=int(row.successful or 0),
            failed=int(row.failed or 0),
            rate_limited=int(row.rate_limited or 0),
            prompt_tokens=int(row.prompt_tokens or 0),
            completion_tokens=int(row.completion_tokens or 0),
            average_latency_ms=(
                float(row.average_latency_ms) if row.average_latency_ms is not None else None
            ),
            first_occurred_at=row.first_occurred_at,
            last_occurred_at=row.last_occurred_at,
        )

    async def top_commands(
        self,
        *,
        guild_id: int | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[CommandUsage]:
        statement = select(
            UsageEvent.command,
            func.count().label("invocations"),
            func.count().filter(UsageEvent.status == UsageStatus.ERROR).label("failures"),
        ).group_by(UsageEvent.command)
        if guild_id is not None:
            statement = statement.where(UsageEvent.guild_id == guild_id)
        for condition in _time_filters(since=since, until=None):
            statement = statement.where(condition)
        statement = statement.order_by(func.count().desc()).limit(limit)
        rows = (await self._session.execute(statement)).all()
        return [
            CommandUsage(
                command=row.command,
                invocations=int(row.invocations or 0),
                failures=int(row.failures or 0),
            )
            for row in rows
        ]

    async def provider_breakdown(
        self,
        *,
        guild_id: int | None = None,
        since: datetime | None = None,
        limit: int = 10,
    ) -> list[ProviderUsage]:
        statement = select(
            UsageEvent.provider,
            UsageEvent.model,
            func.count().label("invocations"),
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0).label("completion_tokens"),
        ).group_by(UsageEvent.provider, UsageEvent.model)
        if guild_id is not None:
            statement = statement.where(UsageEvent.guild_id == guild_id)
        for condition in _time_filters(since=since, until=None):
            statement = statement.where(condition)
        statement = statement.order_by(func.count().desc()).limit(limit)
        rows = (await self._session.execute(statement)).all()
        return [
            ProviderUsage(
                provider=row.provider,
                model=row.model,
                invocations=int(row.invocations or 0),
                prompt_tokens=int(row.prompt_tokens or 0),
                completion_tokens=int(row.completion_tokens or 0),
            )
            for row in rows
        ]

    async def daily_series(
        self, *, guild_id: int | None = None, since: datetime | None = None, limit: int = 30
    ) -> list[DailyUsage]:
        day = func.date_trunc("day", UsageEvent.occurred_at).label("day")
        statement = select(
            day,
            func.count().label("invocations"),
            func.coalesce(func.sum(UsageEvent.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageEvent.completion_tokens), 0).label("completion_tokens"),
        ).group_by(day)
        if guild_id is not None:
            statement = statement.where(UsageEvent.guild_id == guild_id)
        for condition in _time_filters(since=since, until=None):
            statement = statement.where(condition)
        statement = statement.order_by(day.desc()).limit(limit)
        rows = (await self._session.execute(statement)).all()
        return [
            DailyUsage(
                day=row.day,
                invocations=int(row.invocations or 0),
                prompt_tokens=int(row.prompt_tokens or 0),
                completion_tokens=int(row.completion_tokens or 0),
            )
            for row in rows
        ]

    async def events_page(
        self,
        *,
        guild_id: int | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> Page[UsageEvent]:
        statement = select(UsageEvent)
        if guild_id is not None:
            statement = statement.where(UsageEvent.guild_id == guild_id)
        if cursor:
            position = decode_cursor(cursor)
            try:
                identifier = int(position.identifier)
            except ValueError as exc:
                raise InvalidInputError("the pagination cursor is not valid") from exc
            # See the note in the message repository: an explicit keyset predicate
            # keeps ordering stable when several events share a timestamp.
            statement = statement.where(
                or_(
                    UsageEvent.occurred_at < position.created_at,
                    and_(
                        UsageEvent.occurred_at == position.created_at,
                        UsageEvent.id < identifier,
                    ),
                )
            )
        statement = statement.order_by(UsageEvent.occurred_at.desc(), UsageEvent.id.desc()).limit(
            limit + 1
        )
        rows = list(await self._session.scalars(statement))
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = (
            encode_cursor(items[-1].occurred_at, items[-1].id) if has_more and items else None
        )
        return Page(items=items, next_cursor=next_cursor, limit=limit)

    async def purge_older_than(self, cutoff: datetime) -> int:
        result = await self._session.execute(
            delete(UsageEvent).where(UsageEvent.occurred_at < cutoff)
        )
        return affected_rows(result)

    async def purge_for_user(self, user_id: int) -> int:
        result = await self._session.execute(
            delete(UsageEvent).where(UsageEvent.user_id == user_id)
        )
        return affected_rows(result)

    async def count(self) -> int:
        total = await self._session.scalar(select(func.count()).select_from(UsageEvent))
        return int(total or 0)
