"""Cache key namespacing and TTL policy.

Keys are built in exactly one place so the prefix, separators and TTLs stay
consistent. Nothing here contains user data or identifiers: identifiers are
always supplied by the caller from a real interaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["DEFAULT_TTLS", "SEPARATOR", "CacheKeys", "CacheNamespace"]

SEPARATOR = ":"


class CacheNamespace(StrEnum):
    """Logical partitions, each with its own TTL policy."""

    SETTINGS = "settings"
    CONVERSATION = "conversation"
    TRANSCRIPT = "transcript"
    RATE_LIMIT = "ratelimit"
    IDEMPOTENCY = "idempotency"
    PROVIDER_HEALTH = "provider_health"
    MODEL_CATALOG = "model_catalog"


#: Seconds each namespace keeps an entry by default. Operational tuning values,
#: overridable in code per call - never user data.
DEFAULT_TTLS: dict[CacheNamespace, int] = {
    CacheNamespace.SETTINGS: 300,
    CacheNamespace.CONVERSATION: 120,
    CacheNamespace.TRANSCRIPT: 900,
    CacheNamespace.RATE_LIMIT: 120,
    CacheNamespace.IDEMPOTENCY: 900,
    CacheNamespace.PROVIDER_HEALTH: 60,
    CacheNamespace.MODEL_CATALOG: 3600,
}


@dataclass(frozen=True, slots=True)
class CacheKeys:
    """Builds every key this application uses."""

    prefix: str

    def key(self, namespace: CacheNamespace, *parts: object) -> str:
        joined = SEPARATOR.join(str(part) for part in parts)
        if joined:
            return f"{self.prefix}{SEPARATOR}{namespace.value}{SEPARATOR}{joined}"
        return f"{self.prefix}{SEPARATOR}{namespace.value}"

    def settings(self, guild_id: int) -> str:
        return self.key(CacheNamespace.SETTINGS, guild_id)

    def conversation_window(self, conversation_id: object) -> str:
        return self.key(CacheNamespace.CONVERSATION, conversation_id)

    def transcript(self, *, channel_id: int, first_message_id: int, last_message_id: int) -> str:
        return self.key(CacheNamespace.TRANSCRIPT, channel_id, first_message_id, last_message_id)

    def rate_limit(self, *, scope: str, identifier: object, window_seconds: int) -> str:
        return self.key(CacheNamespace.RATE_LIMIT, scope, identifier, window_seconds)

    def idempotency(self, identifier: str) -> str:
        return self.key(CacheNamespace.IDEMPOTENCY, identifier)

    def provider_health(self, provider: str) -> str:
        return self.key(CacheNamespace.PROVIDER_HEALTH, provider)

    def model_catalog(self, provider: str) -> str:
        return self.key(CacheNamespace.MODEL_CATALOG, provider)

    def namespace_pattern(self, namespace: CacheNamespace) -> str:
        return f"{self.prefix}{SEPARATOR}{namespace.value}{SEPARATOR}*"
