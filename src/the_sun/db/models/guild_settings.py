"""Per-guild configuration.

A row is provisioned automatically the first time the bot is used in a guild, so
installing The Sun in a new server needs no manual database work and no guild ID
appears anywhere in the source. Column defaults are the documented defaults; an
administrator overrides them later through the settings command.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from the_sun.db.base import Base, DiscordSnowflake, JsonColumn
from the_sun.db.models.mixins import TimestampMixin

__all__ = ["GuildSettings"]


class GuildSettings(TimestampMixin, Base):
    """Settings for one Discord guild."""

    __tablename__ = "guild_settings"

    guild_id: Mapped[int] = mapped_column(DiscordSnowflake, primary_key=True, autoincrement=False)

    #: Provider name from AI_PROVIDERS; NULL means "use DEFAULT_PROVIDER".
    ai_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Model identifier; NULL means "use the provider's configured default_model".
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Overrides the built-in prompt template for this guild only.
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Number of previous messages assembled into a context window.
    history_length: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("20"))
    #: Whether explicit /memory entries may be stored and injected.
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: Whether command output defaults to a private (ephemeral) reply.
    ephemeral_responses: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    #: Rate limits and quotas. NULL means "no guild-specific override".
    rate_limit_per_user_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_per_guild_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_token_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Role IDs whose members may administer The Sun in this guild.
    admin_role_ids: Mapped[list[int]] = mapped_column(
        JsonColumn, nullable=False, server_default=text("'[]'")
    )
    #: Channel IDs where commands are allowed. Empty means "every channel".
    allowed_channel_ids: Mapped[list[int]] = mapped_column(
        JsonColumn, nullable=False, server_default=text("'[]'")
    )
    #: Days to keep conversation history. NULL means "keep indefinitely".
    history_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
