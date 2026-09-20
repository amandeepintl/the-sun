"""Circuit breaker.

Repeated failures against one provider temporarily remove it from the candidate
list, so a broken upstream cannot make every user wait for the same timeout. The
state lives in Redis rather than in process memory: multiple shards then share one
view of provider health, and a restart does not forget an outage.

State machine
    closed    - normal operation
    open      - failures reached the threshold; requests are refused until
                ``open_until`` passes
    half_open - the window has elapsed; one probing request is allowed, and its
                outcome decides whether the breaker closes or reopens
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from the_sun.cache import CacheBackend, CacheKeys
from the_sun.errors import CacheUnavailableError

__all__ = ["CircuitBreaker", "CircuitSnapshot", "CircuitState", "build_breaker"]

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Whether a provider may be used right now."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """Point-in-time view of one provider's health."""

    provider: str
    state: CircuitState
    failures: int
    open_until: datetime | None = None
    last_failure_at: datetime | None = None

    @property
    def allows_request(self) -> bool:
        return self.state is not CircuitState.OPEN

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "failures": self.failures,
            "open_until": self.open_until.isoformat() if self.open_until else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
        }


class CircuitBreaker:
    """Tracks consecutive failures per provider in the shared cache."""

    def __init__(
        self,
        cache: CacheBackend,
        keys: CacheKeys,
        *,
        clock: Callable[[], datetime],
        failure_threshold: int = 5,
        reset_seconds: int = 60,
    ) -> None:
        self._cache = cache
        self._keys = keys
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds

    def _now(self) -> datetime:
        return self._clock()

    async def snapshot(self, provider: str) -> CircuitSnapshot:
        """Read the current state, tolerating an unavailable cache."""
        payload = await self._read(provider)
        return self._snapshot_from_payload(provider, payload)

    def _snapshot_from_payload(self, provider: str, payload: dict[str, object]) -> CircuitSnapshot:
        failures = _as_count(payload.get("failures"))
        open_until = _parse_timestamp(payload.get("open_until"))
        last_failure_at = _parse_timestamp(payload.get("last_failure_at"))
        now = self._now()
        if open_until is None:
            state = CircuitState.CLOSED
        elif open_until > now:
            state = CircuitState.OPEN
        else:
            state = CircuitState.HALF_OPEN
        return CircuitSnapshot(
            provider=provider,
            state=state,
            failures=failures,
            open_until=open_until,
            last_failure_at=last_failure_at,
        )

    async def allow_request(self, provider: str) -> CircuitSnapshot:
        """Return the snapshot; callers refuse the request when ``allows_request`` is false."""
        return await self.snapshot(provider)

    async def record_success(self, provider: str) -> None:
        """A successful call clears the failure history and closes the breaker."""
        try:
            await self._cache.delete(self._keys.provider_health(provider))
        except CacheUnavailableError as exc:
            logger.warning("could not reset the provider circuit", extra={"error": str(exc)})

    async def record_failure(self, provider: str) -> CircuitSnapshot:
        """Count a failure and open the breaker once the threshold is reached."""
        payload = await self._read(provider)
        failures = _as_count(payload.get("failures")) + 1
        now = self._now()
        payload["failures"] = failures
        payload["last_failure_at"] = now.isoformat()
        if failures >= self._failure_threshold:
            payload["open_until"] = (now + timedelta(seconds=self._reset_seconds)).isoformat()
        await self._write(provider, payload)
        snapshot = self._snapshot_from_payload(provider, payload)
        if snapshot.state is CircuitState.OPEN:
            logger.warning(
                "provider circuit opened",
                extra={
                    "provider": provider,
                    "failures": failures,
                    "open_until": payload.get("open_until"),
                },
            )
        return snapshot

    async def report(self, providers: list[str]) -> list[CircuitSnapshot]:
        """Snapshots for several providers, used by the health command."""
        return [await self.snapshot(provider) for provider in providers]

    async def reset(self, provider: str) -> None:
        """Force a provider back into service (operator action)."""
        await self.record_success(provider)

    # ------------------------------------------------------------------ #
    # Storage
    # ------------------------------------------------------------------ #
    async def _read(self, provider: str) -> dict[str, object]:
        try:
            raw = await self._cache.get(self._keys.provider_health(provider))
        except CacheUnavailableError as exc:
            logger.warning("circuit state is unreadable", extra={"error": str(exc)})
            return {}
        if raw is None:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _write(self, provider: str, payload: dict[str, object]) -> None:
        # The entry outlives the open window so that a half-open probe still finds
        # the failure history it needs.
        ttl = max(self._reset_seconds * 4, self._reset_seconds + 60)
        try:
            await self._cache.set(
                self._keys.provider_health(provider),
                json.dumps(payload).encode("utf-8"),
                ttl_seconds=ttl,
            )
        except CacheUnavailableError as exc:
            logger.warning("circuit state could not be written", extra={"error": str(exc)})


def _as_count(value: object) -> int:
    """Read a counter from cached JSON, treating anything unexpected as zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_breaker(
    cache: CacheBackend,
    keys: CacheKeys,
    *,
    clock: Callable[[], datetime],
    failure_threshold: int,
    reset_seconds: int,
) -> CircuitBreaker:
    """Construct a breaker from resolved configuration."""
    return CircuitBreaker(
        cache,
        keys,
        clock=clock,
        failure_threshold=failure_threshold,
        reset_seconds=reset_seconds,
    )
