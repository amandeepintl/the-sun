"""Conversations.

A conversation groups the messages exchanged about one thread of discussion. Its
``scope`` decides who shares it: the default is one conversation per user per
Discord channel, which keeps a busy channel's histories separate without any
configuration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from the_sun.db.base import Base, DiscordSnowflake
from the_sun.db.models.columns import enum_column
from the_sun.db.models.enums import ConversationScope
from the_sun.db.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover
    from the_sun.db.models.message import Message

__all__ = ["Conversation"]


class Conversation(TimestampMixin, Base):
    """A conversation thread, resolved from real Discord context."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    scope: Mapped[ConversationScope] = mapped_column(
        enum_column(ConversationScope, name="conversation_scope"),
        nullable=False,
        default=ConversationScope.USER_CHANNEL,
    )

    #: Real Discord context. NULL means "not applicable for this scope".
    guild_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    user_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)

    #: The provider/model actually used, recorded for reproducibility.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Optional human-readable title, derived from the first prompt.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_conversations_guild_channel_user", "guild_id", "channel_id", "user_id"),
        Index("ix_conversations_last_activity_at", "last_activity_at"),
    )
