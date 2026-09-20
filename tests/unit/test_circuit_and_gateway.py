"""Circuit breaker state machine and gateway failover."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.fakes import (
    InMemoryCacheBackend,
    attach_transport,
    chat_completion_payload,
    error_payload,
    json_response,
)

from the_sun.ai import ChatMessage, CircuitState, OpenAICompatibleProvider, create_registry
from the_sun.ai.circuit import CircuitBreaker
from the_sun.ai.retry import RetryExhaustedError
from the_sun.cache import CacheKeys
from the_sun.config import Settings
from the_sun.errors import (
    ProviderAuthError,
    ProviderCircuitOpenError,
    ProviderUpstreamError,
)
from the_sun.services.ai_gateway import AIGateway, build_gateway

KEYS = CacheKeys(prefix="thesun-test")

PROVIDERS = {
    "primary": {
        "type": "openai_compatible",
        "base_url": "https://primary.invalid/v1",
        "api_key": "primary-key",
        "default_model": "primary-model",
    },
    "secondary": {
        "type": "openai_compatible",
        "base_url": "https://secondary.invalid/v1",
        "default_model": "secondary-model",
    },
}


class Clock:
    """Manually advanced clock so circuit windows are deterministic."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def _settings(*, fallbacks: str = "", retries: str = "1") -> Settings:
    return Settings(
        _env_file=None,
        DISCORD_TOKEN="unit-test-discord-token",
        DATABASE_URL="postgresql://unit:unit@localhost:5432/unit",
        REDIS_URL="redis://localhost:6379/0",
        AI_PROVIDERS=json.dumps(PROVIDERS),
        DEFAULT_PROVIDER="primary",
        AI_FALLBACK_PROVIDERS=fallbacks,
        AI_MAX_RETRIES=retries,
        AI_CIRCUIT_FAILURE_THRESHOLD="2",
        AI_CIRCUIT_RESET_SECONDS="30",
    )


def _breaker(cache: InMemoryCacheBackend, clock: Clock) -> CircuitBreaker:
    return CircuitBreaker(cache, KEYS, clock=clock, failure_threshold=2, reset_seconds=30)


def _gateway(settings: Settings, cache: InMemoryCacheBackend, clock: Clock) -> AIGateway:
    return build_gateway(
        create_registry(settings.ai),
        cache=cache,
        keys=KEYS,
        ai_config=settings.ai,
        clock=clock,
    )


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #
async def test_breaker_starts_closed() -> None:
    snapshot = await _breaker(InMemoryCacheBackend(), Clock()).snapshot("primary")
    assert snapshot.state is CircuitState.CLOSED
    assert snapshot.failures == 0
    assert snapshot.allows_request is True


async def test_breaker_opens_only_after_the_threshold() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    breaker = _breaker(cache, clock)
    first = await breaker.record_failure("primary")
    assert first.state is CircuitState.CLOSED
    assert first.allows_request is True
    second = await breaker.record_failure("primary")
    assert second.state is CircuitState.OPEN
    assert second.allows_request is False
    assert second.open_until is not None


async def test_open_breaker_half_opens_after_the_window() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    breaker = _breaker(cache, clock)
    await breaker.record_failure("primary")
    await breaker.record_failure("primary")
    clock.advance(seconds=31)
    snapshot = await breaker.snapshot("primary")
    assert snapshot.state is CircuitState.HALF_OPEN
    assert snapshot.allows_request is True


async def test_success_closes_the_breaker_and_clears_history() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    breaker = _breaker(cache, clock)
    await breaker.record_failure("primary")
    await breaker.record_failure("primary")
    await breaker.record_success("primary")
    snapshot = await breaker.snapshot("primary")
    assert snapshot.state is CircuitState.CLOSED
    assert snapshot.failures == 0
    assert await cache.get(KEYS.provider_health("primary")) is None


async def test_breakers_share_state_through_the_cache() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    await _breaker(cache, clock).record_failure("primary")
    await _breaker(cache, clock).record_failure("primary")
    assert (await _breaker(cache, clock).snapshot("primary")).state is CircuitState.OPEN


async def test_each_provider_has_its_own_circuit() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    breaker = _breaker(cache, clock)
    await breaker.record_failure("primary")
    await breaker.record_failure("primary")
    assert (await breaker.snapshot("primary")).state is CircuitState.OPEN
    assert (await breaker.snapshot("secondary")).state is CircuitState.CLOSED


async def test_corrupt_circuit_state_is_treated_as_closed() -> None:
    cache, clock = InMemoryCacheBackend(), Clock()
    await cache.set(KEYS.provider_health("primary"), b"{not json")
    assert (await _breaker(cache, clock).snapshot("primary")).state is CircuitState.CLOSED


async def test_report_lists_every_candidate() -> None:
    report = await _breaker(InMemoryCacheBackend(), Clock()).report(["primary", "secondary"])
    assert [snapshot.provider for snapshot in report] == ["primary", "secondary"]


# --------------------------------------------------------------------------- #
# Gateway
# --------------------------------------------------------------------------- #
class _CallCounter:
    """Counts requests for one provider and returns its configured outcome."""

    def __init__(self, name: str, calls: dict[str, int], outcome: object) -> None:
        self._name = name
        self._calls = calls
        self.outcome = outcome

    def respond(self) -> httpx.Response:
        self._calls[self._name] += 1
        outcome = self.outcome
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, httpx.Response)
        return outcome


def _install(gateway: AIGateway, behaviours: dict[str, object]) -> dict[str, int]:
    """Give every provider a transport double and count its calls."""
    calls: dict[str, int] = dict.fromkeys(behaviours, 0)

    for name, outcome in behaviours.items():
        provider = gateway.registry.get(name)
        assert isinstance(provider, OpenAICompatibleProvider)
        counter = _CallCounter(name, calls, outcome)

        def responder(request: httpx.Request, counter: _CallCounter = counter) -> httpx.Response:
            return counter.respond()

        attach_transport(provider, responder)
    return calls


async def test_gateway_answers_from_the_guild_provider() -> None:
    settings = _settings()
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    calls = _install(
        gateway,
        {"primary": json_response(chat_completion_payload("hello", model="primary-model"))},
    )
    outcome = await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert outcome.result.content == "hello"
    assert outcome.provider == "primary"
    assert outcome.attempts == 1
    assert outcome.failover_used is False
    assert calls == {"primary": 1}
    await gateway.registry.aclose()


async def test_gateway_fails_over_to_the_configured_fallback() -> None:
    settings = _settings(fallbacks="secondary")
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    calls = _install(
        gateway,
        {
            "primary": json_response(error_payload("upstream exploded"), status_code=503),
            "secondary": json_response(
                chat_completion_payload("from fallback", model="secondary-model")
            ),
        },
    )
    outcome = await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert outcome.result.content == "from fallback"
    assert outcome.provider == "secondary"
    assert outcome.skipped_providers == []
    assert calls == {"primary": 1, "secondary": 1}
    await gateway.registry.aclose()


async def test_gateway_does_not_retry_authentication_failures() -> None:
    """A rejected key will be rejected again, so it is never retried — but a
    healthy fallback may still answer the user."""
    settings = _settings(fallbacks="secondary", retries="3")
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    calls = _install(
        gateway,
        {
            "primary": json_response(error_payload("bad key"), status_code=401),
            "secondary": json_response(
                chat_completion_payload("served by fallback", model="secondary-model")
            ),
        },
    )
    outcome = await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert calls == {"primary": 1, "secondary": 1}, "a rejected key must not be retried"
    assert outcome.provider == "secondary"
    assert outcome.result.content == "served by fallback"
    await gateway.registry.aclose()


async def test_authentication_failure_surfaces_when_there_is_nowhere_to_fail_over() -> None:
    settings = _settings(retries="3")
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    calls = _install(gateway, {"primary": json_response(error_payload("bad key"), status_code=401)})
    with pytest.raises(ProviderAuthError):
        await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert calls == {"primary": 1}
    await gateway.registry.aclose()


async def test_gateway_skips_a_provider_whose_circuit_is_open() -> None:
    settings = _settings(fallbacks="secondary")
    cache, clock = InMemoryCacheBackend(), Clock()
    gateway = _gateway(settings, cache, clock)
    calls = _install(
        gateway,
        {"secondary": json_response(chat_completion_payload("other", model="secondary-model"))},
    )
    breaker = _breaker(cache, clock)
    await breaker.record_failure("primary")
    await breaker.record_failure("primary")

    outcome = await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert outcome.provider == "secondary"
    assert outcome.skipped_providers == ["primary"]
    assert calls == {"secondary": 1}
    await gateway.registry.aclose()


async def test_gateway_opens_the_circuit_after_repeated_failures() -> None:
    settings = _settings()
    cache, clock = InMemoryCacheBackend(), Clock()
    gateway = _gateway(settings, cache, clock)
    _install(gateway, {"primary": json_response(error_payload("down"), status_code=500)})
    for _ in range(2):
        with pytest.raises(RetryExhaustedError) as error:
            await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
        assert isinstance(error.value.error, ProviderUpstreamError)

    assert (await gateway.breaker.snapshot("primary")).state is CircuitState.OPEN
    with pytest.raises(ProviderCircuitOpenError):
        await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    await gateway.registry.aclose()


async def test_gateway_recovers_after_the_circuit_window() -> None:
    settings = _settings()
    cache, clock = InMemoryCacheBackend(), Clock()
    gateway = _gateway(settings, cache, clock)
    behaviour: dict[str, object] = {
        "primary": json_response(error_payload("down"), status_code=500)
    }
    _install(gateway, behaviour)
    for _ in range(2):
        with pytest.raises(RetryExhaustedError):
            await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")

    clock.advance(seconds=31)
    behaviour["primary"] = json_response(
        chat_completion_payload("recovered", model="primary-model")
    )
    _install(gateway, behaviour)

    outcome = await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    assert outcome.result.content == "recovered"
    assert (await gateway.breaker.snapshot("primary")).state is CircuitState.CLOSED
    await gateway.registry.aclose()


async def test_gateway_raises_when_every_provider_is_sidelined() -> None:
    settings = _settings(fallbacks="secondary")
    cache, clock = InMemoryCacheBackend(), Clock()
    gateway = _gateway(settings, cache, clock)
    breaker = _breaker(cache, clock)
    for name in ("primary", "secondary"):
        await breaker.record_failure(name)
        await breaker.record_failure(name)
    with pytest.raises(ProviderCircuitOpenError):
        await gateway.chat([ChatMessage.user("hi")], guild_provider="primary")
    await gateway.registry.aclose()


async def test_chain_deduplicates_and_ignores_unknown_fallbacks() -> None:
    settings = _settings(fallbacks="secondary,primary")
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    assert gateway.chain(guild_provider="primary") == ["primary", "secondary"]
    assert gateway.chain(guild_provider="removed") == ["primary", "secondary"]
    await gateway.registry.aclose()


async def test_gateway_trims_the_context_to_its_budget() -> None:
    settings = _settings()
    gateway = _gateway(settings, InMemoryCacheBackend(), Clock())
    fitted = gateway.fit_to_context([ChatMessage.user("x" * 60_000)])
    assert len(fitted) <= 1
    await gateway.registry.aclose()


async def test_streaming_uses_the_first_healthy_provider() -> None:
    from tests.fakes import streaming_response

    settings = _settings(fallbacks="secondary")
    cache, clock = InMemoryCacheBackend(), Clock()
    gateway = _gateway(settings, cache, clock)
    _install(
        gateway,
        {"primary": streaming_response("part ", "two", model="primary-model")},
    )
    chunks = [chunk.delta async for chunk in gateway.stream([ChatMessage.user("hi")])]
    assert "".join(chunks) == "part two"
    await gateway.registry.aclose()


async def test_circuit_report_covers_the_chain() -> None:
    gateway = _gateway(_settings(fallbacks="secondary"), InMemoryCacheBackend(), Clock())
    report = await gateway.circuit_report(guild_provider="secondary")
    assert [snapshot.provider for snapshot in report] == ["secondary", "primary"]
    await gateway.registry.aclose()
