"""Rate limiting.

Windows are counted in Redis by an atomic Lua script, so two shards cannot both
grant the last slot of a window. A guild may configure its own per-minute limits;
when it has not, the deployment defaults apply.

If Redis is unreachable the check fails open: losing the cache must not make the
bot unusable for everyone. That decision is logged and counted, so an outage is
visible in the metrics rather than silent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from the_sun.errors import CacheUnavailableError, RateLimitedError
from the_sun.observability import get_metrics
from the_sun.services.base import ServiceContext
from the_sun.services.settings_service import GuildSettingsView

__all__ = ["WINDOW_SECONDS", "RateLimitDecision", "RateLimitService"]

logger = logging.getLogger(__name__)

#: Length of the counting window. One minute is the unit guild settings use.
WINDOW_SECONDS = 60
WINDOW_MILLISECONDS = WINDOW_SECONDS * 1000


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a rate limit check."""

    allowed: bool
    scope: str
    limit: int
    used: int
    retry_after_seconds: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class RateLimitService:
    """Counts invocations per member and per guild."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context

    @property
    def enabled(self) -> bool:
        return self._context.settings.rate_limit_enabled

    def user_limit(self, settings: GuildSettingsView) -> int:
        return (
            settings.rate_limit_per_user_per_minute
            or self._context.settings.default_rate_limit_per_user_per_minute
        )

    def guild_limit(self, settings: GuildSettingsView) -> int:
        return (
            settings.rate_limit_per_guild_per_minute
            or self._context.settings.default_rate_limit_per_guild_per_minute
        )

    async def check(
        self, *, user_id: int, guild_id: int | None, settings: GuildSettingsView
    ) -> RateLimitDecision:
        """Count this invocation against both windows.

        The member's window is checked first so a heavy guild cannot be blamed on
        one person, and a refused check never consumes the guild's allowance.
        """
        if not self.enabled:
            return RateLimitDecision(allowed=True, scope="disabled", limit=0, used=0)

        member = await self._count(
            scope="user",
            identifier=user_id,
            limit=self.user_limit(settings),
            guild_id=guild_id,
        )
        if not member.allowed:
            return member
        if guild_id is None:
            return member
        guild = await self._count(
            scope="guild", identifier=guild_id, limit=self.guild_limit(settings), guild_id=guild_id
        )
        if not guild.allowed:
            return guild
        return guild

    async def ensure_allowed(
        self, *, user_id: int, guild_id: int | None, settings: GuildSettingsView
    ) -> RateLimitDecision:
        """Check the windows and raise the typed error when one is exhausted."""
        decision = await self.check(user_id=user_id, guild_id=guild_id, settings=settings)
        if decision.allowed:
            return decision
        raise RateLimitedError(
            f"{decision.scope} window exhausted: {decision.used}/{decision.limit}",
            retry_after_seconds=float(decision.retry_after_seconds),
        )

    async def _count(
        self, *, scope: str, identifier: int, limit: int, guild_id: int | None
    ) -> RateLimitDecision:
        key = self._context.keys.rate_limit(
            scope=scope, identifier=identifier, window_seconds=WINDOW_SECONDS
        )
        now_ms = int(self._context.clock().timestamp() * 1000)
        member = f"{now_ms}-{uuid.uuid4().hex}"
        try:
            allowed, used, retry_after = await self._context.cache.run_script(
                "sliding_window_limiter",
                keys=[key],
                args=[now_ms, WINDOW_MILLISECONDS, limit, member],
            )
        except CacheUnavailableError as exc:
            get_metrics().counter(
                "sun_rate_limit_degraded_total",
                "Rate limit checks that could not be counted because the cache was unavailable",
            ).increment(scope=scope)
            logger.warning(
                "rate limit check failed open",
                extra={"scope": scope, "guild_id": guild_id, "error": str(exc)},
            )
            return RateLimitDecision(allowed=True, scope=scope, limit=limit, used=0)
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            scope=scope,
            limit=limit,
            used=int(used),
            retry_after_seconds=int(retry_after),
        )
