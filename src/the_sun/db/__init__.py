"""Database layer: declarative models, engine/session management.

Only repositories and Alembic migrations are allowed to contain SQL.
"""

from __future__ import annotations

from the_sun.db.base import Base, DiscordSnowflake, JsonColumn
from the_sun.db.models import (
    Conversation,
    ConversationScope,
    GuildSettings,
    Memory,
    MemorySource,
    Message,
    MessageKind,
    MessageRole,
    UsageEvent,
    UsageStatus,
    UsageSurface,
    User,
)
from the_sun.db.session import Database, create_database

__all__ = [
    "Base",
    "Conversation",
    "ConversationScope",
    "Database",
    "DiscordSnowflake",
    "GuildSettings",
    "JsonColumn",
    "Memory",
    "MemorySource",
    "Message",
    "MessageKind",
    "MessageRole",
    "UsageEvent",
    "UsageStatus",
    "UsageSurface",
    "User",
    "create_database",
]
