"""ORM models.

Importing this package registers every table on ``Base.metadata``, which is what
Alembic's autogenerate and the test fixtures rely on.
"""

from __future__ import annotations

from the_sun.db.base import Base
from the_sun.db.models.conversation import Conversation
from the_sun.db.models.enums import (
    ConversationScope,
    MemorySource,
    MessageKind,
    MessageRole,
    UsageStatus,
    UsageSurface,
)
from the_sun.db.models.guild_settings import GuildSettings
from the_sun.db.models.memory import Memory
from the_sun.db.models.message import Message
from the_sun.db.models.usage_event import UsageEvent
from the_sun.db.models.user import User

__all__ = [
    "Base",
    "Conversation",
    "ConversationScope",
    "GuildSettings",
    "Memory",
    "MemorySource",
    "Message",
    "MessageKind",
    "MessageRole",
    "UsageEvent",
    "UsageStatus",
    "UsageSurface",
    "User",
]
