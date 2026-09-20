"""Discord users the bot has interacted with."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from the_sun.db.base import Base, DiscordSnowflake
from the_sun.db.models.mixins import TimestampMixin

__all__ = ["User"]


class User(TimestampMixin, Base):
    """A Discord account, created lazily the first time it interacts."""

    __tablename__ = "users"

    #: Discord snowflake. Assigned by Discord, never by this application, which
    #: is why the primary key is not generated here.
    user_id: Mapped[int] = mapped_column(DiscordSnowflake, primary_key=True, autoincrement=False)
    #: Most recent display name observed from a real interaction.
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
