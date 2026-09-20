"""Retry policy behaviour."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from the_sun.ai import RetryPolicy, execute_with_retries
from the_sun.ai.retry import RetryExhaustedError
from the_sun.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUpstreamError,
)

POLICY = RetryPolicy(attempts=3, base_seconds=0.1, max_seconds=1.0, jitter_ratio=0.0)


def test_policy_rejects_a_zero_attempt_budget() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(attempts=0)


def test_backoff_grows_exponentially_and_is_capped() -> None:
    policy = RetryPolicy(attempts=5, base_seconds=1.0, max_seconds=4.0, jitter_ratio=0.0)
    assert [policy.delay_for(attempt, jitter=0.5) for attempt in (1, 2, 3, 4, 5)] == [
        1.0,
        2.0,
        4.0,
        4.0,
        4.0,
    ]


def test_jitter_stays_inside_the_configured_band() -> None:
    policy = RetryPolicy(attempts=3, base_seconds=1.0, max_seconds=10.0, jitter_ratio=0.25)
    assert policy.delay_for(1, jitter=0.0) == pytest.approx(0.75)
    assert policy.delay_for(1, jitter=0.5) == pytest.approx(1.0)
    assert policy.delay_for(1, jitter=1.0) == pytest.approx(1.25)


def test_delay_never_goes_negative() -> None:
    policy = RetryPolicy(attempts=2, base_seconds=0.1, max_seconds=8.0, jitter_ratio=4.0)
    assert policy.delay_for(1, jitter=0.0) >= 0.0


async def test_successful_first_attempt_does_not_sleep() -> None:
    slept: list[float] = []

    async def operation(attempt: int) -> str:
        return f"attempt {attempt}"

    value, attempts = await execute_with_retries(operation, policy=POLICY, sleep=_recorder(slept))
    assert value == "attempt 1"
    assert attempts == 1
    assert slept == []


async def test_retryable_failure_is_retried_with_backoff() -> None:
    seen: list[int] = []
    slept: list[float] = []
    retries: list[tuple[int, str, float]] = []

    async def operation(attempt: int) -> str:
        seen.append(attempt)
        if attempt < 3:
            raise ProviderTimeoutError("too slow")
        return "recovered"

    value, attempts = await execute_with_retries(
        operation,
        policy=POLICY,
        on_retry=lambda attempt, error, delay: retries.append(
            (attempt, type(error).__name__, delay)
        ),
        sleep=_recorder(slept),
        random_source=lambda: 0.5,
    )
    assert value == "recovered"
    assert attempts == 3
    assert seen == [1, 2, 3]
    assert len(slept) == 2
    assert slept == sorted(slept), "backoff must not shrink between attempts"
    assert [entry[1] for entry in retries] == ["ProviderTimeoutError", "ProviderTimeoutError"]


async def test_upstream_errors_are_retried() -> None:
    calls = 0

    async def operation(attempt: int) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderUpstreamError("bad gateway")
        return "ok"

    value, attempts = await execute_with_retries(
        operation, policy=POLICY, sleep=_recorder([]), random_source=lambda: 0.5
    )
    assert value == "ok"
    assert attempts == 2


async def test_non_retryable_failures_are_reported_immediately() -> None:
    calls = 0

    async def operation(attempt: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderAuthError("bad key")

    with pytest.raises(ProviderAuthError):
        await execute_with_retries(operation, policy=POLICY, sleep=_recorder([]))
    assert calls == 1


async def test_malformed_responses_are_not_retried() -> None:
    async def operation(attempt: int) -> str:
        raise ProviderBadResponseError("nonsense")

    with pytest.raises(ProviderBadResponseError):
        await execute_with_retries(operation, policy=POLICY, sleep=_recorder([]))


async def test_exhausting_every_attempt_reports_how_many_there_were() -> None:
    async def operation(attempt: int) -> str:
        raise ProviderTimeoutError(f"attempt {attempt} timed out")

    with pytest.raises(RetryExhaustedError) as error:
        await execute_with_retries(operation, policy=POLICY, sleep=_recorder([]))
    assert error.value.attempts == 3
    assert isinstance(error.value.error, ProviderTimeoutError)


async def test_rate_limit_retry_after_wins_over_computed_backoff() -> None:
    slept: list[float] = []

    async def operation(attempt: int) -> str:
        if attempt == 1:
            error = ProviderRateLimitError("slow down")
            error.retry_after_seconds = 2.5
            raise error
        return "ok"

    value, attempts = await execute_with_retries(
        operation,
        policy=RetryPolicy(attempts=2, base_seconds=0.1, jitter_ratio=0.0),
        sleep=_recorder(slept),
        random_source=lambda: 0.5,
    )
    assert value == "ok"
    assert attempts == 2
    assert slept == [2.5]


def _recorder(sink: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        sink.append(delay)

    return sleep
