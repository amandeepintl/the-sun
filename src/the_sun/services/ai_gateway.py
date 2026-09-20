"""AI gateway.

Everything that needs an AI answer goes through here, so the resilience policy is
applied once instead of in every command:

1. the guild's provider is tried first, followed by the configured fallbacks;
2. providers whose circuit is open are skipped until their window elapses;
3. each provider gets a bounded number of retries for retryable failures only;
4. outcomes feed the breaker and the metrics registry.

The gateway never invents an answer. If every provider fails, the caller gets the
typed error describing what actually went wrong.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from the_sun.ai import (
    AIProvider,
    ChatMessage,
    ChatResult,
    ModelInfo,
    ProviderRegistry,
    StreamChunk,
    trim_to_budget,
)
from the_sun.ai.circuit import CircuitBreaker, CircuitSnapshot, CircuitState, build_breaker
from the_sun.ai.retry import RetryPolicy, execute_with_retries
from the_sun.cache import CacheBackend, CacheKeys
from the_sun.config import AIConfig
from the_sun.errors import ProviderCircuitOpenError, ProviderError, TheSunError

__all__ = ["AIGateway", "ChatOutcome", "build_gateway"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    """A provider answer plus how it was obtained."""

    result: ChatResult
    provider: str
    attempts: int
    skipped_providers: list[str] = field(default_factory=list)

    @property
    def failover_used(self) -> bool:
        return bool(self.skipped_providers)


class AIGateway:
    """Provider selection, retries, failover and circuit breaking."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        breaker: CircuitBreaker,
        ai_config: AIConfig,
        max_context_tokens: int | None = None,
    ) -> None:
        self._registry = registry
        self._breaker = breaker
        self._config = ai_config
        self._max_context_tokens = max_context_tokens or ai_config.max_context_tokens

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def max_context_tokens(self) -> int:
        return self._max_context_tokens

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            attempts=self._config.max_retries,
            base_seconds=self._config.retry_base_seconds,
            max_seconds=self._config.retry_max_seconds,
        )

    def chain(self, *, guild_provider: str | None = None) -> list[str]:
        """Ordered provider names to attempt, without duplicates.

        The guild's choice comes first, then the deployment default (so a guild can
        still be served when its own provider is unhealthy), then every configured
        fallback. Names that are no longer configured are ignored.
        """
        candidates = (
            guild_provider,
            self._config.default_provider,
            *self._config.fallback_providers,
        )
        ordered: list[str] = []
        for name in candidates:
            if name and name in self._config.providers and name not in ordered:
                ordered.append(name)
        if not ordered:
            ordered.append(self._config.default_provider)
        return ordered

    def fit_to_context(self, messages: Sequence[ChatMessage]) -> list[ChatMessage]:
        """Trim a conversation to this gateway's context budget."""
        return trim_to_budget(messages, max_tokens=self._max_context_tokens)

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        guild_provider: str | None = None,
        guild_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        context_already_fitted: bool = False,
    ) -> ChatOutcome:
        """Answer a prompt, failing over between providers as needed."""
        prepared = list(messages) if context_already_fitted else self.fit_to_context(messages)
        candidates = self.chain(guild_provider=guild_provider)
        skipped: list[str] = []
        last_error: TheSunError | None = None

        for position, name in enumerate(candidates):
            snapshot = await self._breaker.snapshot(name)
            if not snapshot.allows_request:
                skipped.append(name)
                logger.info(
                    "skipping provider with an open circuit",
                    extra={
                        "provider": name,
                        "open_until": snapshot.open_until.isoformat()
                        if snapshot.open_until
                        else None,
                    },
                )
                continue

            # A guild's model choice only makes sense for the provider it was
            # chosen for; fallbacks use their own configured default.
            model = guild_model if position == 0 and guild_provider == name else None
            provider = self._registry.get(name)

            # Bound explicitly rather than closed over, so the attempt never
            # accidentally uses a later iteration's provider or model.
            def attempt_call(
                _attempt: int,
                provider: AIProvider = provider,
                model: str | None = model,
            ) -> Awaitable[ChatResult]:
                return provider.chat(
                    prepared,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

            try:
                result, attempts = await execute_with_retries(
                    attempt_call, policy=self.retry_policy()
                )
            except TheSunError as error:
                last_error = error
                await self._breaker.record_failure(name)
                logger.warning(
                    "provider failed, considering the next candidate",
                    extra={"provider": name, "error": f"{type(error).__name__}: {error}"},
                )
                continue

            await self._breaker.record_success(name)
            return ChatOutcome(
                result=result, provider=name, attempts=attempts, skipped_providers=skipped
            )

        if last_error is None:
            raise ProviderCircuitOpenError(
                "every configured provider is temporarily unavailable",
                provider=candidates[0] if candidates else None,
            )
        if skipped and isinstance(last_error, ProviderError):
            raise last_error
        raise last_error

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        guild_provider: str | None = None,
        guild_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a response from the first healthy provider.

        Streaming cannot be retried mid-response without duplicating text, so the
        provider is chosen first and its failures are recorded for the next call.
        """
        prepared = self.fit_to_context(messages)
        candidates = self.chain(guild_provider=guild_provider)
        for position, name in enumerate(candidates):
            snapshot = await self._breaker.snapshot(name)
            if not snapshot.allows_request:
                continue
            model = guild_model if position == 0 and guild_provider == name else None
            provider = self._registry.get(name)
            streamed_any = False
            try:
                async for chunk in provider.stream(
                    prepared, model=model, temperature=temperature, max_tokens=max_tokens
                ):
                    streamed_any = True
                    yield chunk
            except TheSunError as error:
                await self._breaker.record_failure(name)
                if streamed_any:
                    # Part of the answer is already out; report instead of retrying.
                    raise
                logger.warning(
                    "streaming provider failed before producing output",
                    extra={"provider": name, "error": str(error)},
                )
                continue
            await self._breaker.record_success(name)
            return
        raise ProviderCircuitOpenError("no provider is available for a streaming request")

    async def list_models(self, provider: str | None = None) -> list[ModelInfo]:
        """Live model catalogue for one provider (used by settings autocomplete)."""
        return await self._registry.fetch_models(provider)

    async def circuit_report(self, *, guild_provider: str | None = None) -> list[CircuitSnapshot]:
        """Health of every candidate provider, for the health command."""
        return await self._breaker.report(self.chain(guild_provider=guild_provider))

    async def is_healthy(self, provider: str) -> bool:
        snapshot = await self._breaker.snapshot(provider)
        return snapshot.state is not CircuitState.OPEN


def build_gateway(
    registry: ProviderRegistry,
    *,
    cache: CacheBackend,
    keys: CacheKeys,
    ai_config: AIConfig,
    clock: Callable[[], datetime],
) -> AIGateway:
    """Construct the gateway with its breaker bound to the shared cache."""
    breaker = build_breaker(
        cache,
        keys,
        clock=clock,
        failure_threshold=ai_config.circuit_failure_threshold,
        reset_seconds=ai_config.circuit_reset_seconds,
    )
    return AIGateway(registry, breaker=breaker, ai_config=ai_config)
