"""The path every AI command takes.

One place enforces the per-guild policy - rate limits, the daily token allowance,
which provider and model to use, whose memories may be read - and one place
records what actually happened. Commands decide *what* to ask; this service decides
whether the guild allows it and how it is stored.

Nothing here can produce an answer on its own. If every provider fails, the typed
error from the gateway is what the member sees.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from the_sun.ai import Usage
from the_sun.ai.retry import RetryExhaustedError
from the_sun.db.models import MessageKind, MessageRole, UsageStatus, UsageSurface
from the_sun.errors import (
    InvalidInputError,
    PersistenceError,
    QuotaExceededError,
    RateLimitedError,
    TheSunError,
)
from the_sun.logging_setup import current_correlation_id
from the_sun.observability import get_metrics
from the_sun.services.base import ServiceContext
from the_sun.services.conversation_service import ConversationService
from the_sun.services.memory_service import MemoryService
from the_sun.services.prompt_builder import PromptBuilder
from the_sun.services.rate_limit_service import RateLimitService
from the_sun.services.settings_service import GuildSettingsView
from the_sun.services.usage_service import UsageRecord, UsageService

__all__ = ["AIAnswer", "AIRequest", "AIService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AIRequest:
    """One AI invocation, as a command wants it to happen."""

    command: str
    instruction: str
    user_id: int
    settings: GuildSettingsView
    guild_id: int | None = None
    channel_id: int | None = None
    kind: MessageKind = MessageKind.CHAT
    surface: UsageSurface = UsageSurface.SLASH_COMMAND
    discord_message_id: int | None = None
    use_history: bool = True
    use_memories: bool = True
    store_turns: bool = True
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class AIAnswer:
    """What a provider actually returned, plus how it was obtained."""

    content: str
    provider: str
    model: str
    usage: Usage | None
    latency_ms: int
    attempts: int
    failover_used: bool
    conversation_id: uuid.UUID | None = None
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int | None:
        return self.usage.total_tokens if self.usage else None


class AIService:
    """Applies guild policy, calls the gateway and records the outcome."""

    def __init__(self, context: ServiceContext) -> None:
        self._context = context
        self.prompts = PromptBuilder(context)
        self.conversations = ConversationService(context)
        self.memories = MemoryService(context)
        self.rate_limiter = RateLimitService(context)
        self.usage = UsageService(context)

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def respond(self, request: AIRequest) -> AIAnswer:
        """Answer one request, honouring the guild's limits and settings."""
        instruction = request.instruction.strip()
        if not instruction:
            raise InvalidInputError(
                "the instruction was empty",
                public_message="There was nothing to ask. Provide a question or some text.",
            )

        try:
            await self.rate_limiter.ensure_allowed(
                user_id=request.user_id, guild_id=request.guild_id, settings=request.settings
            )
        except RateLimitedError:
            # A refusal is an invocation too: record it so per-guild statistics
            # and the daily token ledger see every request the member made.
            await self._record(request, status=UsageStatus.RATE_LIMITED, error_code="rate_limited")
            raise
        await self._enforce_token_allowance(request)

        conversation = None
        if request.use_history or request.store_turns:
            conversation = await self.conversations.current(
                guild_id=request.guild_id,
                channel_id=request.channel_id,
                user_id=request.user_id,
                provider=request.settings.ai_provider,
                model=request.settings.ai_model,
            )

        history = (
            await self.conversations.recent_window(
                conversation.id, limit=request.settings.history_length
            )
            if conversation is not None and request.use_history
            else []
        )
        memories = (
            await self.memories.entries_for_prompt(
                user_id=request.user_id,
                guild_id=request.guild_id,
                enabled=request.settings.memory_enabled,
            )
            if request.use_memories
            else []
        )

        messages = self.prompts.assemble(
            guild_prompt=request.settings.system_prompt,
            memories=memories,
            history=history,
            instruction=instruction,
        )

        started = time.perf_counter()
        try:
            outcome = await self._context.ai.chat(
                messages,
                guild_provider=request.settings.ai_provider,
                guild_model=request.settings.ai_model,
                temperature=self._temperature(request),
                max_tokens=request.max_tokens or self._context.settings.ai_max_output_tokens,
            )
        except TheSunError as error:
            latency_ms = int((time.perf_counter() - started) * 1000)
            # Unwrap the retry-exhausted wrapper so telemetry and callers see the
            # real classification instead of a generic retry failure.
            if isinstance(error, RetryExhaustedError):
                error = error.error
            get_metrics().counter(
                "sun_ai_requests_total", "AI requests, by command and outcome"
            ).increment(command=request.command, status="error")
            await self._record(
                request,
                status=UsageStatus.ERROR,
                error_code=type(error).__name__,
                latency_ms=latency_ms,
            )
            # Surface the unwrapped error: a caller should never need to know
            # that retries happened to see what actually went wrong.
            raise error

        latency_ms = int((time.perf_counter() - started) * 1000)
        answer = AIAnswer(
            content=outcome.result.content,
            provider=outcome.provider,
            model=outcome.result.model,
            usage=outcome.result.usage,
            latency_ms=latency_ms,
            attempts=outcome.attempts,
            failover_used=outcome.failover_used,
            conversation_id=conversation.id if conversation else None,
            finish_reason=outcome.result.finish_reason,
        )
        get_metrics().counter(
            "sun_ai_requests_total", "AI requests, by command and outcome"
        ).increment(command=request.command, provider=outcome.provider, status="ok")
        get_metrics().histogram(
            "sun_ai_latency_ms", "End-to-end latency of AI requests in milliseconds"
        ).observe(latency_ms, command=request.command, provider=outcome.provider)

        await self._store_turns(request, answer)
        await self._record(
            request,
            status=UsageStatus.OK,
            provider=answer.provider,
            model=answer.model,
            usage=answer.usage,
            latency_ms=latency_ms,
        )
        return answer

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _temperature(self, request: AIRequest) -> float | None:
        if request.temperature is not None:
            return request.temperature
        return self._context.settings.ai_temperature

    async def _enforce_token_allowance(self, request: AIRequest) -> None:
        """Refuse the request when the guild is out of tokens for today."""
        quota = request.settings.daily_token_quota
        if not quota or request.guild_id is None:
            return
        used = await self.usage.tokens_today(request.guild_id)
        if used < quota:
            return
        await self._record(request, status=UsageStatus.RATE_LIMITED, error_code="quota_exceeded")
        raise QuotaExceededError(
            f"guild {request.guild_id} used {used} of {quota} daily tokens",
            public_message=(
                "This server has reached its daily allowance of "
                f"{quota:,} tokens. An administrator can raise it with `/settings quota`."
            ),
        )

    async def _store_turns(self, request: AIRequest, answer: AIAnswer) -> None:
        """Persist both turns of the exchange.

        Storage is best effort: the member already has their answer, so a database
        problem here is logged instead of turning a success into a failure.
        """
        if not request.store_turns or answer.conversation_id is None:
            return
        try:
            await self.conversations.append(
                conversation_id=answer.conversation_id,
                role=MessageRole.USER,
                kind=request.kind,
                content=request.instruction.strip(),
                discord_guild_id=request.guild_id,
                discord_channel_id=request.channel_id,
                discord_message_id=request.discord_message_id,
            )
            await self.conversations.append(
                conversation_id=answer.conversation_id,
                role=MessageRole.ASSISTANT,
                kind=request.kind,
                content=answer.content,
                discord_guild_id=request.guild_id,
                discord_channel_id=request.channel_id,
                provider=answer.provider,
                model=answer.model,
                prompt_tokens=answer.usage.prompt_tokens if answer.usage else None,
                completion_tokens=answer.usage.completion_tokens if answer.usage else None,
                latency_ms=answer.latency_ms,
            )
        except PersistenceError as exc:
            logger.warning(
                "conversation turns could not be stored",
                extra={"command": request.command, "error": str(exc)},
            )

    async def _record(
        self,
        request: AIRequest,
        *,
        status: UsageStatus,
        provider: str | None = None,
        model: str | None = None,
        usage: Usage | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        """Write the usage event for this invocation."""
        await self.usage.record(
            UsageRecord(
                command=request.command,
                surface=request.surface,
                guild_id=request.guild_id,
                channel_id=request.channel_id,
                user_id=request.user_id,
                provider=provider,
                model=model,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                correlation_id=current_correlation_id(),
            )
        )
