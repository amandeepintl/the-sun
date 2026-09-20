"""Stored conversation turns.

Every row is written because a real user invoked a command. Messages keep the
identifiers of the Discord message they came from or produced, so a summary can
always be traced back to the messages it summarised.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from the_sun.db.base import Base, DiscordSnowflake
from the_sun.db.models.columns import enum_column
from the_sun.db.models.enums import MessageKind, MessageRole
from the_sun.db.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover
    from the_sun.db.models.conversation import Conversation

__all__ = ["Message"]


class Message(TimestampMixin, Base):
    """One user or assistant turn inside a conversation."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[MessageRole] = mapped_column(
        enum_column(MessageRole, name="message_role"), nullable=False
    )
    kind: Mapped[MessageKind] = mapped_column(
        enum_column(MessageKind, name="message_kind"),
        nullable=False,
        default=MessageKind.CHAT,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    #: Real Discord coordinates, when this turn came from or produced a message.
    discord_guild_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    discord_channel_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)
    discord_message_id: Mapped[int | None] = mapped_column(DiscordSnowflake, nullable=True)

    #: Provenance and telemetry, all reported by the provider or measured by us.
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_guild_channel", "discord_guild_id", "discord_channel_id"),
        # Used by the retention purge, which deletes by creation time.
        Index("ix_messages_created_at", "created_at"),
    )
