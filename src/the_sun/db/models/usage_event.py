"""Append-only usage events.

This table is the only source of statistics. Every number a user sees from the
stats command is an aggregation over rows written by real command invocations -
token counts come from the provider's own usage report, latency is measured
locally, and nothing is estimated or invented.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from the_sun.db.base import Base, DiscordSnowflake
from the_sun.db.models.columns import enum_column
from the_sun.db.models.enums import UsageStatus, UsageSurface

__all__ = ["UsageEvent"]


class UsageEvent(Base):
    """One command invocation and its outcome."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: Real Discord context of the invocation.
    guild_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    user_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)

    #: Command name as registered with Discord (for example "ask").
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[UsageSurface] = mapped_column(
        enum_column(UsageSurface, name="usage_surface"),
        nullable=False,
        default=UsageSurface.SLASH_COMMAND,
    )

    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[UsageStatus] = mapped_column(
        enum_column(UsageStatus, name="usage_status"),
        nullable=False,
        default=UsageStatus.OK,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        # Wall clock per INSERT: now() would give every event in one
        # transaction the same timestamp and blur their ordering.
        default=func.clock_timestamp(),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_usage_events_guild_occurred", "guild_id", "occurred_at"),
        Index("ix_usage_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_usage_events_command_occurred", "command", "occurred_at"),
    )
