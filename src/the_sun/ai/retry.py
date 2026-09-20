"""Retry policy for provider calls.

Only failures that were classified as retryable are retried: an authentication
error or a malformed request is reported immediately, because repeating it would
just burn quota. When a provider tells us how long to wait, that wait wins over
the computed backoff.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from the_sun.errors import ProviderRateLimitError, TheSunError

__all__ = ["RetryExhaustedError", "RetryPolicy", "execute_with_retries"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times to try, and how long to wait between attempts."""

    attempts: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 8.0
    #: Fraction of the computed delay used for jitter, so parallel callers do not
    #: retry in lockstep.
    jitter_ratio: float = 0.25

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("a retry policy needs at least one attempt")

    def delay_for(self, attempt: int, *, jitter: float = 0.5) -> float:
        """Delay before ``attempt`` (1-based) runs again.

        ``jitter`` is a unit value from the random source; the default keeps the
        calculation deterministic for callers that do not pass one.
        """
        exponential = self.base_seconds * (2 ** max(0, attempt - 1))
        capped = min(exponential, self.max_seconds)
        spread = capped * self.jitter_ratio
        offset = spread * (2.0 * min(max(jitter, 0.0), 1.0) - 1.0)
        return max(0.0, capped + offset)


class RetryExhaustedError(TheSunError):
    """Raised when the final attempt also failed.

    It keeps the wrapped error's classification and user-facing wording, so
    failover and messaging behave exactly as they would for the original failure
    while telemetry still learns how many attempts were spent.
    """

    def __init__(self, error: TheSunError, attempts: int):
        super().__init__(
            f"{error} (after {attempts} attempt(s))", public_message=error.public_message
        )
        self.retryable = error.retryable
        self.error = error
        self.attempts = attempts


async def execute_with_retries[T](
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    on_retry: Callable[[int, TheSunError, float], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_source: Callable[[], float] = random.random,
) -> tuple[T, int]:
    """Run ``operation`` with bounded retries.

    The operation receives the attempt number (1-based). The returned tuple
    carries the value and how many attempts it took, which callers record as
    telemetry.
    """
    last_error: TheSunError | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            value = await operation(attempt)
        except TheSunError as error:
            last_error = error
            if not error.retryable or attempt >= policy.attempts:
                if attempt >= policy.attempts and error.retryable:
                    raise RetryExhaustedError(error, attempt) from error
                raise
            delay = policy.delay_for(attempt, jitter=random_source())
            honoured = getattr(error, "retry_after_seconds", None)
            if isinstance(error, ProviderRateLimitError) and honoured:
                delay = max(delay, float(honoured))
            if on_retry is not None:
                on_retry(attempt, error, delay)
            logger.warning(
                "retrying provider call",
                extra={
                    "attempt": attempt,
                    "delay_seconds": round(delay, 3),
                    "error": f"{type(error).__name__}: {error}",
                },
            )
            await sleep(delay)
        else:
            return value, attempt
    # Unreachable: the loop either returns or raises.
    raise RetryExhaustedError(last_error or TheSunError("no attempt was made"), policy.attempts)
