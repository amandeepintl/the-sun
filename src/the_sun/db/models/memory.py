"""User memories.

Memory entries are written only when a user explicitly asks for it, and they
belong to that user. A ``guild_id`` of ``NULL`` means the entry applies
everywhere; a non-NULL value scopes it to one server.

PostgreSQL treats ``NULL`` values as distinct in a unique constraint, so
uniqueness is expressed as two partial unique indexes rather than one composite
constraint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from the_sun.db.base import Base, DiscordSnowflake
from the_sun.db.models.columns import enum_column
from the_sun.db.models.enums import MemorySource
from the_sun.db.models.mixins import TimestampMixin

__all__ = ["Memory"]


class Memory(TimestampMixin, Base):
    """A single remembered preference or fact belonging to one user."""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[int] = mapped_column(DiscordSnowflake, nullable=False)
    #: NULL means "applies in every guild the user talks to The Sun in".
    guild_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[MemorySource] = mapped_column(
        enum_column(MemorySource, name="memory_source"),
        nullable=False,
        default=MemorySource.EXPLICIT,
    )

    __table_args__ = (
        Index(
            "uq_memories_user_key_global",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("guild_id IS NULL"),
            sqlite_where=text("guild_id IS NULL"),
        ),
        Index(
            "uq_memories_user_key_scoped",
            "user_id",
            "guild_id",
            "key",
            unique=True,
            postgresql_where=text("guild_id IS NOT NULL"),
            sqlite_where=text("guild_id IS NOT NULL"),
        ),
        Index("ix_memories_user_id", "user_id"),
    )
