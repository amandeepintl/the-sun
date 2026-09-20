"""Retention.

Two things expire: stored conversation turns, per guild window, and usage events,
after the deployment's own window. Nothing is deleted implicitly - a guild's
window only exists when an administrator set one, and the SQL comparison keeps a
row whose window is ``NULL`` - so unconfigured history stays until someone deletes
it explicitly with ``/privacy forget me``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from the_sun.observability import get_metrics
from the_sun.services.base import ServiceContext

__all__ = ["RetentionReport", "RetentionService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """What one purge actually removed."""

    messages_deleted: int
    usage_events_deleted: int
    default_window_days: int | None
    usage_window_days: int
    ran_at: datetime


class RetentionService:
    """Purges stored turns and usage events whose window has elapsed."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    @property
    def interval_hours(self) -> int:
        return self._context.settings.retention_interval_hours

    async def purge(self, *, now: datetime | None = None) -> RetentionReport:
        """Delete expired rows and report the real counts."""
        moment = now or self._context.clock()
        settings = self._context.settings
        usage_window = settings.usage_retention_days
        async with self._context.unit_of_work() as uow:
            messages_deleted = await uow.messages.purge_unretained(
                now=moment, default_retention_days=settings.default_history_retention_days
            )
            usage_deleted = (
                await uow.usage.purge_older_than(cutoff=moment - timedelta(days=usage_window))
                if usage_window > 0
                else 0
            )
        report = RetentionReport(
            messages_deleted=messages_deleted,
            usage_events_deleted=usage_deleted,
            default_window_days=settings.default_history_retention_days,
            usage_window_days=usage_window,
            ran_at=moment,
        )
        if messages_deleted or usage_deleted:
            logger.info(
                "retention purge finished",
                extra={
                    "messages_deleted": messages_deleted,
                    "usage_events_deleted": usage_deleted,
                },
            )
        get_metrics().counter(
            "sun_retention_deleted_total", "Rows removed by the retention purge"
        ).increment(messages_deleted, table="messages")
        get_metrics().counter(
            "sun_retention_deleted_total", "Rows removed by the retention purge"
        ).increment(usage_deleted, table="usage_events")
        return report
