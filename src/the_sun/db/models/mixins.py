"""Reusable column mixes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

__all__ = ["TimestampMixin"]


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the database itself.

    The defaults are SQL expressions evaluated by the database when each
    INSERT runs, not when its transaction started: ``now()`` always returns the
    transaction start time, so every row written inside one transaction (for
    example a user turn and its assistant reply) would share one timestamp and
    their stored order would be ambiguous. ``clock_timestamp()`` is the true
    wall clock at the moment of each INSERT.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.clock_timestamp(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.clock_timestamp(),
        server_default=func.now(),
        onupdate=func.clock_timestamp(),
    )
