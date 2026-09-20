"""Repositories against real PostgreSQL.

Every test writes through the unit of work into a real database and reads the rows
back with real SQL. The surrounding transaction is rolled back, so nothing is left
behind.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from the_sun.db.models import (
    ConversationScope,
    MemorySource,
    MessageKind,
    MessageRole,
    UsageStatus,
    UsageSurface,
)
from the_sun.errors import InvalidInputError, PersistenceError
from the_sun.repositories import UnitOfWork


async def test_user_rows_are_created_then_refreshed(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        created = await uow.users.upsert_seen(unique_id, "first name")
        assert created.user_id == unique_id
        assert await uow.users.count() >= 1

    async with uow_factory() as uow:
        refreshed = await uow.users.upsert_seen(unique_id, "new name")
        assert refreshed.display_name == "new name"
        assert await uow.users.get(unique_id) is not None
        assert await uow.users.delete(unique_id) is True
        assert await uow.users.get(unique_id) is None


async def test_guild_settings_are_provisioned_lazily(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        assert await uow.guild_settings.get(unique_id) is None
        created = await uow.guild_settings.get_or_create(unique_id)
        assert created.guild_id == unique_id
        # Defaults come from the schema, not from code.
        assert created.history_length == 20
        assert created.memory_enabled is True
        assert created.admin_role_ids == []
        assert created.allowed_channel_ids == []

        updated = await uow.guild_settings.update(
            unique_id,
            {"ai_model": "unit-test-model", "admin_role_ids": [11, 22], "memory_enabled": False},
        )
        assert updated.ai_model == "unit-test-model"
        assert updated.admin_role_ids == [11, 22]
        assert updated.memory_enabled is False


async def test_guild_settings_reject_unknown_fields(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        with pytest.raises(InvalidInputError):
            await uow.guild_settings.update(unique_id, {"not_a_setting": 1})


async def test_conversations_are_scoped_to_real_context(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    guild_id, channel_id, user_id = unique_id, unique_id + 1, unique_id + 2
    async with uow_factory() as uow:
        created = await uow.conversations.create(
            scope=ConversationScope.USER_CHANNEL,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            provider="primary",
            model="unit-test-model",
        )
        assert isinstance(created.id, uuid.UUID)

        found = await uow.conversations.get_active(
            scope=ConversationScope.USER_CHANNEL,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
        )
        assert found is not None and found.id == created.id

        # A different user in the same channel gets their own conversation.
        assert (
            await uow.conversations.get_active(
                scope=ConversationScope.USER_CHANNEL,
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id + 99,
            )
            is None
        )

        await uow.conversations.touch(created, provider="primary", model="unit-test-model")
        assert created.provider == "primary"
        assert await uow.conversations.deactivate(created.id) is True
        assert (
            await uow.conversations.get_active(
                scope=ConversationScope.USER_CHANNEL,
                guild_id=guild_id,
                channel_id=channel_id,
                user_id=user_id,
            )
            is None
        )
        assert await uow.conversations.count() >= 1


async def test_messages_round_trip_with_telemetry_and_history_window(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        conversation = await uow.conversations.create(
            scope=ConversationScope.USER_CHANNEL,
            guild_id=unique_id,
            channel_id=unique_id + 1,
            user_id=unique_id + 2,
        )
        for index in range(5):
            await uow.messages.append(
                conversation_id=conversation.id,
                role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
                kind=MessageKind.CHAT,
                content=f"turn {index}",
                discord_guild_id=unique_id,
                discord_channel_id=unique_id + 1,
                discord_message_id=1_000 + index,
                provider="primary",
                model="unit-test-model",
                prompt_tokens=10 + index,
                completion_tokens=20 + index,
                latency_ms=100 + index,
                correlation_id="integration-test",
            )

        window = await uow.messages.recent_window(conversation.id, limit=3)
        assert [message.content for message in window] == ["turn 2", "turn 3", "turn 4"]
        assert window[0].created_at <= window[-1].created_at
        assert await uow.messages.count_for_conversation(conversation.id) == 5

        page = await uow.messages.history_page(conversation.id, limit=2)
        assert [message.content for message in page.items] == ["turn 4", "turn 3"]
        assert page.has_more is True

        assert page.next_cursor is not None
        second = await uow.messages.history_page(conversation.id, limit=2, cursor=page.next_cursor)
        assert [message.content for message in second.items] == ["turn 2", "turn 1"]
        assert second.next_cursor is not None
        third = await uow.messages.history_page(conversation.id, limit=2, cursor=second.next_cursor)
        assert [message.content for message in third.items] == ["turn 0"]
        assert third.has_more is False

        assert await uow.messages.delete_for_user(unique_id + 2) == 5
        assert await uow.messages.count_for_conversation(conversation.id) == 0


async def test_messages_cascade_when_their_conversation_is_deleted(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        conversation = await uow.conversations.create(
            scope=ConversationScope.USER_CHANNEL,
            guild_id=unique_id,
            channel_id=unique_id + 1,
            user_id=unique_id + 2,
        )
        await uow.messages.append(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            kind=MessageKind.CHAT,
            content="a question",
        )
        await uow.session.delete(conversation)
        await uow.session.flush()
        assert await uow.messages.count_for_conversation(conversation.id) == 0


async def test_memories_upsert_list_and_delete(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    guild_id = unique_id
    async with uow_factory() as uow:
        await uow.memories.upsert(user_id=unique_id, key="tone", value="concise")
        await uow.memories.upsert(
            user_id=unique_id, key="timezone", value="Europe/Berlin", guild_id=guild_id
        )
        updated = await uow.memories.upsert(user_id=unique_id, key="tone", value="detailed")
        assert updated.value == "detailed"
        assert updated.source is MemorySource.EXPLICIT

        global_only = await uow.memories.list_for_user(unique_id)
        assert [memory.key for memory in global_only] == ["tone"]

        in_guild = await uow.memories.list_for_user(unique_id, guild_id=guild_id)
        assert [memory.key for memory in in_guild] == ["timezone", "tone"]

        scoped_only = await uow.memories.list_for_user(
            unique_id, guild_id=guild_id, include_global=False
        )
        assert [memory.key for memory in scoped_only] == ["timezone"]

        assert await uow.memories.get(user_id=unique_id, key="tone", guild_id=None) is not None
        assert await uow.memories.delete(user_id=unique_id, key="tone", guild_id=None) is True
        assert await uow.memories.delete(user_id=unique_id, key="tone", guild_id=None) is False
        assert await uow.memories.delete_all_for_user(unique_id) == 1


async def test_memory_keys_are_unique_per_scope(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    """The partial unique index makes a duplicate global key a database error."""
    insert = text(
        "INSERT INTO memories (id, user_id, guild_id, key, value, source)"
        " VALUES (:id, :user_id, NULL, 'language', 'German', 'explicit')"
    )
    async with uow_factory() as uow:
        await uow.memories.upsert(user_id=unique_id, key="language", value="French")
        with pytest.raises(PersistenceError) as error:
            await uow.session.execute(insert, {"id": uuid.uuid4(), "user_id": unique_id})
    assert isinstance(error.value.__cause__, IntegrityError)


async def test_usage_events_aggregate_from_real_rows(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    guild_id, user_id = unique_id, unique_id + 1
    async with uow_factory() as uow:
        for index in range(4):
            await uow.usage.append(
                command="ask" if index % 2 == 0 else "summarize",
                surface=UsageSurface.SLASH_COMMAND,
                guild_id=guild_id,
                channel_id=unique_id + 2,
                user_id=user_id,
                provider="primary",
                model="unit-test-model",
                prompt_tokens=100 + index,
                completion_tokens=50 + index,
                latency_ms=200 + index,
                status=UsageStatus.OK if index < 3 else UsageStatus.ERROR,
                error_code=None if index < 3 else "ProviderTimeoutError",
                correlation_id="integration-test",
            )
        await uow.usage.append_many(
            [
                {
                    "command": "translate",
                    "surface": UsageSurface.CONTEXT_MENU,
                    "guild_id": guild_id,
                    "user_id": user_id,
                    "status": UsageStatus.OK,
                }
            ]
        )

        summary = await uow.usage.summary(guild_id=guild_id)
        assert summary.invocations == 5
        assert summary.successful == 4
        assert summary.failed == 1
        assert summary.rate_limited == 0
        assert summary.prompt_tokens == sum(100 + index for index in range(4))
        assert summary.average_latency_ms is not None
        assert summary.first_occurred_at is not None
        assert summary.last_occurred_at is not None

        user_summary = await uow.usage.summary(user_id=user_id)
        assert user_summary.invocations >= 5

        top = await uow.usage.top_commands(guild_id=guild_id)
        assert {entry.command for entry in top} == {"ask", "summarize", "translate"}
        ask = next(entry for entry in top if entry.command == "ask")
        assert ask.invocations == 2
        assert ask.failures == 1

        providers = await uow.usage.provider_breakdown(guild_id=guild_id)
        assert providers[0].provider == "primary"
        assert providers[0].invocations == 4

        daily = await uow.usage.daily_series(guild_id=guild_id)
        assert daily and daily[0].invocations == 5

        first_page = await uow.usage.events_page(guild_id=guild_id, limit=2)
        assert len(first_page.items) == 2
        assert first_page.has_more is True
        second_page = await uow.usage.events_page(
            guild_id=guild_id, limit=2, cursor=first_page.next_cursor
        )
        assert len(second_page.items) == 2
        third_page = await uow.usage.events_page(
            guild_id=guild_id, limit=2, cursor=second_page.next_cursor
        )
        assert len(third_page.items) == 1
        assert third_page.has_more is False
        seen = {event.id for event in [*first_page.items, *second_page.items, *third_page.items]}
        assert len(seen) == 5

        cutoff = datetime.now(tz=UTC) - timedelta(seconds=1)
        assert await uow.usage.purge_older_than(cutoff - timedelta(days=1)) == 5
