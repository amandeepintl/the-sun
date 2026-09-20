"""The AI request path against real PostgreSQL and real Redis.

PostgreSQL, Redis, the gateway, the circuit breaker, the rate limiter, the
repositories and the prompt builder are all the production ones. The only
substitution is the HTTP transport inside the provider adapter, so the suite never
calls an external AI service and never spends anyone's quota - but the exact
payload that would be sent is captured and asserted on.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update
from tests.fakes.http_provider import (
    attach_transport,
    chat_completion_payload,
    error_payload,
    json_response,
)

from the_sun.ai import ChatMessage, OpenAICompatibleProvider
from the_sun.cache import CacheBackend
from the_sun.db.models import ConversationScope, Message, MessageKind, MessageRole, UsageStatus
from the_sun.errors import (
    PersistenceError,
    ProviderCircuitOpenError,
    ProviderUpstreamError,
    QuotaExceededError,
    RateLimitedError,
)
from the_sun.services import (
    AIRequest,
    AIService,
    GuildSettingsView,
    MemoryService,
    RetentionService,
    ServiceContext,
    SettingsService,
)

ANSWER = "Stored answer from the transport double."
PROMPT_TOKENS = 42
COMPLETION_TOKENS = 8


def _view(guild_id: int | None = 0, **overrides: object) -> GuildSettingsView:
    """Guild settings with every override unset unless a test sets one."""
    values: dict[str, object] = {
        "guild_id": guild_id,
        "ai_provider": None,
        "ai_model": None,
        "system_prompt": None,
        "history_length": 6,
        "memory_enabled": True,
        "ephemeral_responses": True,
        "rate_limit_per_user_per_minute": None,
        "rate_limit_per_guild_per_minute": None,
        "daily_token_quota": None,
        "admin_role_ids": [],
        "allowed_channel_ids": [],
        "history_retention_days": None,
        "updated_at": datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return GuildSettingsView(**values)  # type: ignore[arg-type]


def _provider(context: ServiceContext) -> OpenAICompatibleProvider:
    provider = context.providers.get()
    assert isinstance(provider, OpenAICompatibleProvider)
    return provider


def _respond_with(context: ServiceContext, answer: str = ANSWER) -> None:
    """Point every configured provider at a transport that answers successfully."""
    for name in context.providers.names():
        adapter = context.providers.get(name)
        assert isinstance(adapter, OpenAICompatibleProvider)

        def responder(
            request: httpx.Request, adapter: OpenAICompatibleProvider = adapter
        ) -> httpx.Response:
            return json_response(
                chat_completion_payload(
                    answer,
                    model=adapter.config.default_model,
                    prompt_tokens=PROMPT_TOKENS,
                    completion_tokens=COMPLETION_TOKENS,
                )
            )

        attach_transport(adapter, responder)


def _capture_payloads(context: ServiceContext) -> list[dict[str, object]]:
    """Attach transports that answer successfully and record what was sent."""
    captured: list[dict[str, object]] = []
    for name in context.providers.names():
        adapter = context.providers.get(name)
        assert isinstance(adapter, OpenAICompatibleProvider)

        def responder(
            request: httpx.Request, adapter: OpenAICompatibleProvider = adapter
        ) -> httpx.Response:
            captured.append(json.loads(request.content.decode("utf-8")))
            return json_response(
                chat_completion_payload(
                    ANSWER,
                    model=adapter.config.default_model,
                    prompt_tokens=PROMPT_TOKENS,
                    completion_tokens=COMPLETION_TOKENS,
                )
            )

        attach_transport(adapter, responder)
    return captured


@pytest.fixture
def ai(service_context: ServiceContext) -> AIService:
    """The AI service over real infrastructure and a transport double."""
    _respond_with(service_context)
    return AIService(service_context)


# --------------------------------------------------------------------------- #
# The happy path, and what it writes
# --------------------------------------------------------------------------- #
async def test_answer_is_recorded_as_usage_and_as_conversation_turns(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    answer = await ai.respond(
        AIRequest(
            command="ask",
            instruction="What is the capital of the Netherlands?",
            user_id=unique_id + 1,
            settings=_view(unique_id),
            guild_id=unique_id,
            channel_id=unique_id + 2,
        )
    )

    assert answer.content == ANSWER
    assert answer.provider in service_context.providers.names()
    assert answer.usage is not None and answer.usage.prompt_tokens == PROMPT_TOKENS
    assert answer.conversation_id is not None

    async with service_context.unit_of_work() as uow:
        summary = await uow.usage.summary(guild_id=unique_id, user_id=unique_id + 1)
        turns = await uow.messages.recent_window(answer.conversation_id, limit=10)
        conversation = await uow.conversations.get(answer.conversation_id)

    assert summary.invocations == 1
    assert summary.successful == 1
    assert summary.prompt_tokens == PROMPT_TOKENS
    assert summary.completion_tokens == COMPLETION_TOKENS
    assert [turn.role for turn in turns] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert turns[0].content == "What is the capital of the Netherlands?"
    assert turns[1].content == ANSWER
    assert turns[1].provider == answer.provider
    assert turns[1].latency_ms is not None
    assert conversation is not None and conversation.is_active is True


async def test_prompt_carries_memories_and_the_previous_turn(
    service_context: ServiceContext, unique_id: int
) -> None:
    captured = _capture_payloads(service_context)
    await MemoryService(service_context).save(
        user_id=unique_id,
        guild_id=unique_id,
        key="timezone",
        value="I work in UTC+2 and prefer metric units.",
    )

    service = AIService(service_context)
    request = AIRequest(
        command="ask",
        instruction="Remind me what you know about my working hours.",
        user_id=unique_id,
        settings=_view(unique_id),
        guild_id=unique_id,
        channel_id=unique_id + 1,
    )
    await service.respond(request)
    await service.respond(request)

    assert len(captured) == 2
    first, second = captured
    assert first["messages"][0]["role"] == "system"  # type: ignore[index]
    assert "UTC+2" in first["messages"][0]["content"], "the saved memory must reach the prompt"  # type: ignore[index]
    assert second["messages"][-1]["content"] == request.instruction  # type: ignore[index]
    history = second["messages"][1:-1]  # type: ignore[index]
    assert [message["role"] for message in history] == ["user", "assistant"], (
        "the previous turn must be included, in order"
    )
    assert second["model"] == _provider(service_context).default_model


async def test_memories_are_omitted_when_the_guild_disables_them(
    service_context: ServiceContext, unique_id: int
) -> None:
    captured = _capture_payloads(service_context)
    await MemoryService(service_context).save(
        user_id=unique_id, guild_id=unique_id, key="secret", value="classified detail"
    )
    await AIService(service_context).respond(
        AIRequest(
            command="ask",
            instruction="Anything you know?",
            user_id=unique_id,
            settings=_view(unique_id, memory_enabled=False),
            guild_id=unique_id,
        )
    )
    assert "classified detail" not in captured[0]["messages"][0]["content"]  # type: ignore[index]


async def test_history_is_trimmed_to_the_configured_window(
    service_context: ServiceContext, unique_id: int
) -> None:
    captured = _capture_payloads(service_context)
    service = AIService(service_context)
    settings = _view(unique_id, history_length=2)
    for index in range(4):
        await service.respond(
            AIRequest(
                command="ask",
                instruction=f"question {index}",
                user_id=unique_id,
                settings=settings,
                guild_id=unique_id,
            )
        )
    last_history = captured[-1]["messages"][1:-1]  # type: ignore[index]
    assert len(last_history) == 2, "only the newest two turns fit the window"
    assert last_history[-1]["content"] == ANSWER


# --------------------------------------------------------------------------- #
# Policy: rate limits and the daily allowance
# --------------------------------------------------------------------------- #
async def test_rate_limit_refusal_is_recorded_and_raises(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    settings = _view(unique_id, rate_limit_per_user_per_minute=1)
    request = AIRequest(
        command="ask",
        instruction="first",
        user_id=unique_id + 7,
        settings=settings,
        guild_id=unique_id,
    )
    await ai.respond(request)
    with pytest.raises(RateLimitedError) as excinfo:
        await ai.respond(request)
    assert excinfo.value.retry_after_seconds

    async with service_context.unit_of_work() as uow:
        summary = await uow.usage.summary(guild_id=unique_id, user_id=unique_id + 7)
    assert summary.invocations == 2
    assert summary.rate_limited == 1
    assert summary.successful == 1


async def test_daily_token_allowance_refuses_once_it_is_used_up(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    request = AIRequest(
        command="ask",
        instruction="spend the allowance",
        user_id=unique_id + 8,
        settings=_view(unique_id, daily_token_quota=10),
        guild_id=unique_id,
    )
    await ai.respond(request)  # the provider reports 42 + 8 tokens
    with pytest.raises(QuotaExceededError):
        await ai.respond(request)
    assert await ai.usage.tokens_today(unique_id) >= 10


async def test_settings_changes_are_what_the_ai_path_enforces(
    service_context: ServiceContext, unique_id: int
) -> None:
    settings_service = SettingsService(service_context)
    await settings_service.update(unique_id, {"daily_token_quota": 5, "history_length": 3})
    view = await settings_service.get(unique_id)
    assert view.daily_token_quota == 5
    assert view.history_length == 3

    _respond_with(service_context)
    with pytest.raises(QuotaExceededError):
        await AIService(service_context).respond(
            AIRequest(
                command="ask",
                instruction="over the allowance",
                user_id=unique_id,
                settings=view,
                guild_id=unique_id,
            )
        )
    async with service_context.unit_of_work() as uow:
        summary = await uow.usage.summary(guild_id=unique_id)
    assert summary.rate_limited == 1


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
async def test_missing_usage_does_not_invent_tokens(
    service_context: ServiceContext, unique_id: int
) -> None:
    provider = _provider(service_context)
    attach_transport(
        provider,
        lambda request: json_response(
            {
                "id": "no-usage",
                "model": provider.config.default_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": ANSWER},
                        "finish_reason": "stop",
                    }
                ],
            }
        ),
    )
    service = AIService(service_context)
    answer = await service.respond(
        AIRequest(
            command="ask",
            instruction="no usage reported",
            user_id=unique_id,
            settings=_view(unique_id),
            guild_id=unique_id,
        )
    )
    assert answer.usage is None
    assert await service.usage.tokens_today(unique_id) == 0


async def test_provider_failure_is_recorded_and_surfaced(
    service_context: ServiceContext, unique_id: int
) -> None:
    provider = _provider(service_context)
    attach_transport(
        provider, lambda request: json_response(error_payload("upstream exploded"), status_code=500)
    )
    with pytest.raises(ProviderUpstreamError):
        await AIService(service_context).respond(
            AIRequest(
                command="explain",
                instruction="this will fail",
                user_id=unique_id,
                settings=_view(unique_id),
                guild_id=unique_id,
            )
        )

    async with service_context.unit_of_work() as uow:
        summary = await uow.usage.summary(guild_id=unique_id, user_id=unique_id)
        events = await uow.usage.events_page(guild_id=unique_id, limit=5)
    assert summary.failed == 1
    assert summary.successful == 0
    assert [event.status for event in events.items] == [UsageStatus.ERROR]
    assert events.items[0].error_code == "ProviderUpstreamError"


async def test_streaming_failure_before_any_output_is_not_duplicated(
    service_context: ServiceContext,
) -> None:
    provider = _provider(service_context)
    calls = {"count": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return json_response(error_payload("stream broke"), status_code=502)

    attach_transport(provider, responder)
    with pytest.raises(ProviderCircuitOpenError):
        async for _ in service_context.ai.stream(
            [ChatMessage.user("hello")], guild_provider=provider.name
        ):
            pytest.fail("no chunk may be emitted")
    assert calls["count"] >= 1


async def test_a_failed_write_rolls_the_whole_unit_of_work_back(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    await ai.respond(
        AIRequest(
            command="ask",
            instruction="kept",
            user_id=unique_id,
            settings=_view(unique_id),
            guild_id=unique_id,
        )
    )
    async with service_context.unit_of_work() as uow:
        before = await uow.usage.summary(guild_id=unique_id)
        before_turns = await uow.messages.count()
        with pytest.raises(PersistenceError):
            await uow.messages.append(
                conversation_id=uuid.uuid4(),  # no such conversation
                role=MessageRole.USER,
                kind=MessageKind.CHAT,
                content="never stored",
            )
    async with service_context.unit_of_work() as uow:
        after = await uow.usage.summary(guild_id=unique_id)
        turns = await uow.messages.count()
    assert after.invocations == before.invocations
    assert turns == before_turns


# --------------------------------------------------------------------------- #
# Conversation shape
# --------------------------------------------------------------------------- #
async def test_conversations_are_isolated_between_members_and_channels(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    settings = _view(unique_id)
    identifiers = []
    for user, channel in ((unique_id, 1), (unique_id + 1, 1), (unique_id, 2)):
        answer = await ai.respond(
            AIRequest(
                command="ask",
                instruction="who am I?",
                user_id=user,
                settings=settings,
                guild_id=unique_id,
                channel_id=channel,
            )
        )
        identifiers.append(answer.conversation_id)
    assert len(set(identifiers)) == 3


async def test_reset_starts_a_new_thread_and_keeps_the_old_turns(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    request = AIRequest(
        command="ask",
        instruction="before the reset",
        user_id=unique_id,
        settings=_view(unique_id),
        guild_id=unique_id,
        channel_id=1,
    )
    first = await ai.respond(request)
    assert first.conversation_id is not None

    assert await ai.conversations.reset(guild_id=unique_id, channel_id=1, user_id=unique_id) is True
    second = await ai.respond(request)
    assert second.conversation_id is not None
    assert second.conversation_id != first.conversation_id

    async with service_context.unit_of_work() as uow:
        old_turns = await uow.messages.count_for_conversation(first.conversation_id)
    assert old_turns == 2, "the previous conversation's turns must still exist"


async def test_direct_message_requests_use_the_global_scope(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    answer = await ai.respond(
        AIRequest(
            command="ask",
            instruction="hello from a DM",
            user_id=unique_id,
            settings=_view(guild_id=None),
            guild_id=None,
            channel_id=None,
        )
    )
    assert answer.conversation_id is not None
    async with service_context.unit_of_work() as uow:
        conversation = await uow.conversations.get(answer.conversation_id)
    assert conversation is not None
    assert conversation.guild_id is None
    assert conversation.scope is ConversationScope.USER


async def test_usage_totals_match_what_the_provider_reported(
    service_context: ServiceContext, ai: AIService, unique_id: int
) -> None:
    settings = _view(unique_id)
    for index in range(3):
        await ai.respond(
            AIRequest(
                command="ask",
                instruction=f"request {index}",
                user_id=unique_id,
                settings=settings,
                guild_id=unique_id,
            )
        )
    summary = await ai.usage.summary(guild_id=unique_id)
    assert summary.invocations == 3
    assert summary.prompt_tokens == PROMPT_TOKENS * 3
    assert summary.completion_tokens == COMPLETION_TOKENS * 3
    assert summary.average_latency_ms is not None
    assert await ai.usage.tokens_today(unique_id) == (PROMPT_TOKENS + COMPLETION_TOKENS) * 3


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
async def test_retention_deletes_only_what_the_window_allows(
    service_context: ServiceContext, unique_id: int
) -> None:
    await SettingsService(service_context).update(unique_id, {"history_retention_days": 7})

    async with service_context.unit_of_work() as uow:
        conversation = await uow.conversations.create(
            scope=ConversationScope.USER_CHANNEL,
            guild_id=unique_id,
            channel_id=unique_id,
            user_id=unique_id,
        )
        old = await uow.messages.append(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            kind=MessageKind.CHAT,
            content="long ago",
        )
        await uow.messages.append(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            kind=MessageKind.CHAT,
            content="just now",
        )
        await uow.session.execute(
            update(Message)
            .where(Message.id == old.id)
            .values(created_at=datetime.now(tz=UTC) - timedelta(days=30))
        )

    report = await RetentionService(service_context).purge()
    assert report.messages_deleted >= 1

    async with service_context.unit_of_work() as uow:
        remaining = await uow.messages.recent_window(conversation.id, limit=10)
    assert [turn.content for turn in remaining] == ["just now"]


async def test_history_no_guild_configured_is_never_deleted(
    service_context: ServiceContext, unique_id: int
) -> None:
    async with service_context.unit_of_work() as uow:
        conversation = await uow.conversations.create(
            scope=ConversationScope.USER,
            guild_id=None,
            channel_id=None,
            user_id=unique_id,
        )
        turn = await uow.messages.append(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            kind=MessageKind.CHAT,
            content="must survive",
        )
        await uow.session.execute(
            update(Message)
            .where(Message.id == turn.id)
            .values(created_at=datetime.now(tz=UTC) - timedelta(days=3650))
        )

    report = await RetentionService(service_context).purge()
    assert report.default_window_days is None
    async with service_context.unit_of_work() as uow:
        remaining = await uow.messages.recent_window(conversation.id, limit=10)
    assert [row.content for row in remaining] == ["must survive"]


async def test_settings_really_round_trip_through_redis(
    service_context: ServiceContext, cache: CacheBackend, unique_id: int
) -> None:
    view = await SettingsService(service_context).get(unique_id)
    assert view.guild_id == unique_id
    raw = await cache.get(service_context.keys.settings(unique_id))
    assert raw is not None, "the settings the AI path reads must be cached in Redis"
