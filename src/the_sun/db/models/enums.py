"""Enumerations stored in the database.

All of them are persisted as short strings with a check constraint
(``native_enum=False``) so that adding a member is an ordinary migration rather
than a PostgreSQL ``ALTER TYPE`` dance.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ConversationScope",
    "MemorySource",
    "MessageKind",
    "MessageRole",
    "UsageStatus",
    "UsageSurface",
]


class MessageRole(StrEnum):
    """Who produced a stored message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MessageKind(StrEnum):
    """Which use case produced a stored message."""

    CHAT = "chat"
    SUMMARY = "summary"
    EXPLAIN = "explain"
    TRANSLATE = "translate"
    CONTEXT_ACTION = "context_action"


class ConversationScope(StrEnum):
    """How a conversation is shared between Discord members."""

    USER_CHANNEL = "user_channel"
    CHANNEL = "channel"
    USER = "user"
    GUILD = "guild"


class MemorySource(StrEnum):
    """How a memory entry was created."""

    EXPLICIT = "explicit"
    AUTO = "auto"


class UsageStatus(StrEnum):
    """Outcome of a command invocation."""

    OK = "ok"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class UsageSurface(StrEnum):
    """Where the invocation came from."""

    SLASH_COMMAND = "slash_command"
    CONTEXT_MENU = "context_menu"
    SYSTEM = "system"
