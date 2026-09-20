"""Explicit memory and the deletion path ``/privacy forget`` uses.

Against real PostgreSQL, so the unique indexes, the ``NULL`` scoping and the
deletion cascade are the production ones.
"""

from __future__ import annotations

import pytest

from the_sun.db.models import (
    ConversationScope,
    MemorySource,
    MessageKind,
    MessageRole,
    UsageStatus,
    UsageSurface,
)
from the_sun.errors import InvalidInputError
from the_sun.services import ConversationService, MemoryService, ServiceContext
from the_sun.services.usage_service import UsageRecord


async def test_saved_memory_is_returned_for_its_scope(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    saved = await memories.save(
        user_id=unique_id, guild_id=unique_id, key="Timezone", value="  UTC+2, metric units  "
    )
    assert saved.key == "timezone", "keys are normalised"
    assert saved.value == "UTC+2, metric units"
    assert saved.source is MemorySource.EXPLICIT

    in_guild = await memories.list_for_user(unique_id, guild_id=unique_id)
    assert [entry.key for entry in in_guild] == ["timezone"]
    elsewhere = await memories.list_for_user(unique_id, guild_id=unique_id + 1)
    assert elsewhere == [], "a guild-scoped memory must not leak into another guild"
    in_direct_message = await memories.list_for_user(unique_id, guild_id=None)
    assert in_direct_message == [], "a guild-scoped memory must not leak into DMs"


async def test_global_memory_is_visible_everywhere(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="name", value="Ada")

    for guild in (None, unique_id, unique_id + 1):
        entries = await memories.list_for_user(unique_id, guild_id=guild)
        assert any(entry.key == "name" for entry in entries), f"missing in scope {guild}"


async def test_saving_the_same_key_updates_it_rather_than_duplicating(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="editor", value="vim")
    await memories.save(user_id=unique_id, guild_id=None, key="editor", value="emacs")
    entries = await memories.list_for_user(unique_id, guild_id=None)
    assert len(entries) == 1
    assert entries[0].value == "emacs"


async def test_a_scoped_and_a_global_entry_with_the_same_key_coexist(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="editor", value="vim")
    await memories.save(user_id=unique_id, guild_id=unique_id, key="editor", value="emacs")
    in_guild = await memories.list_for_user(unique_id, guild_id=unique_id)
    assert {entry.value for entry in in_guild} == {"vim", "emacs"}

    assert await memories.delete(user_id=unique_id, guild_id=unique_id, key="editor") is True
    remaining = await memories.list_for_user(unique_id, guild_id=unique_id)
    assert [entry.value for entry in remaining] == ["vim"], "the global entry must survive"


async def test_deleting_a_missing_memory_reports_it(
    service_context: ServiceContext, unique_id: int
) -> None:
    assert (
        await MemoryService(service_context).delete(
            user_id=unique_id, guild_id=unique_id, key="never-saved"
        )
        is False
    )


async def test_clear_removes_only_the_requested_scope(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="global", value="everywhere")
    await memories.save(user_id=unique_id, guild_id=unique_id, key="scoped", value="here")

    removed = await memories.clear(user_id=unique_id, guild_id=unique_id, include_global=False)
    assert removed == 1
    remaining = await memories.list_for_user(unique_id, guild_id=unique_id)
    assert [entry.key for entry in remaining] == ["global"]

    removed = await memories.clear(user_id=unique_id, guild_id=unique_id, include_global=True)
    assert removed == 1
    assert await memories.list_for_user(unique_id, guild_id=None) == []


async def test_clear_in_a_direct_message_removes_everything(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="one", value="1")
    await memories.save(user_id=unique_id, guild_id=unique_id, key="two", value="2")
    assert await memories.clear(user_id=unique_id, guild_id=None, include_global=True) == 2


async def test_the_entry_cap_is_enforced(
    service_context: ServiceContext, unique_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    memories = MemoryService(service_context)
    # The cap comes from configuration, so lower it on this instance only;
    # monkeypatch restores the real value afterwards.
    monkeypatch.setattr(service_context.settings, "memory_max_entries", 2)
    await memories.save(user_id=unique_id, guild_id=None, key="one", value="1")
    await memories.save(user_id=unique_id, guild_id=None, key="two", value="2")

    with pytest.raises(InvalidInputError) as excinfo:
        await memories.save(user_id=unique_id, guild_id=None, key="three", value="3")
    assert "limit" in excinfo.value.public_message

    # Updating an existing key is still allowed at the cap.
    updated = await memories.save(user_id=unique_id, guild_id=None, key="two", value="22")
    assert updated.value == "22"


async def test_unusable_keys_and_values_are_refused(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    for bad_key in ("", "   ", "!!!"):
        with pytest.raises(InvalidInputError):
            await memories.save(user_id=unique_id, guild_id=None, key=bad_key, value="x")
    with pytest.raises(InvalidInputError):
        await memories.save(user_id=unique_id, guild_id=None, key="ok", value="   ")


async def test_memories_can_be_kept_apart_from_other_members(
    service_context: ServiceContext, unique_id: int
) -> None:
    memories = MemoryService(service_context)
    await memories.save(user_id=unique_id, guild_id=None, key="mine", value="value")
    assert await memories.list_for_user(unique_id + 1, guild_id=None) == []


async def test_privacy_forget_removes_every_trace_of_a_member(
    service_context: ServiceContext, unique_id: int
) -> None:
    """The exact deletion path ``/privacy forget`` runs, against real tables."""
    conversations = ConversationService(service_context)
    memories = MemoryService(service_context)

    conversation = await conversations.current(
        guild_id=unique_id, channel_id=unique_id + 1, user_id=unique_id
    )
    await conversations.append(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        kind=MessageKind.CHAT,
        content="please forget this",
    )
    await conversations.append(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        kind=MessageKind.CHAT,
        content="stored answer",
    )
    dm = await conversations.current(guild_id=None, channel_id=None, user_id=unique_id)
    await conversations.append(
        conversation_id=dm.id,
        role=MessageRole.USER,
        kind=MessageKind.CHAT,
        content="and this",
    )
    await memories.save(user_id=unique_id, guild_id=None, key="global", value="everywhere")
    await memories.save(user_id=unique_id, guild_id=unique_id, key="scoped", value="here")
    async with service_context.unit_of_work() as uow:
        await uow.users.upsert_seen(unique_id, "Ada")
        await uow.usage.append(command="ask", surface=UsageSurface.SLASH_COMMAND, user_id=unique_id)
        await uow.usage.append(
            command="stats",
            surface=UsageSurface.SLASH_COMMAND,
            user_id=unique_id,
            status=UsageStatus.OK,
        )

    assert await conversations.count_for_user(unique_id) == 3
    async with service_context.unit_of_work() as uow:
        assert await uow.memories.count_for_user(unique_id) == 2
        assert (await uow.usage.summary(user_id=unique_id)).invocations == 2

    deleted_conversations, deleted_turns = await conversations.delete_for_user(unique_id)
    removed_memories = await memories.clear(user_id=unique_id, guild_id=None, include_global=True)
    async with service_context.unit_of_work() as uow:
        removed_events = await uow.usage.purge_for_user(unique_id)
        profile_removed = await uow.users.delete(unique_id)

    assert deleted_conversations == 2
    assert deleted_turns == 3
    assert removed_memories == 2
    assert removed_events == 2
    assert profile_removed is True

    async with service_context.unit_of_work() as uow:
        assert await uow.messages.count_for_user(unique_id) == 0
        assert await uow.memories.count_for_user(unique_id) == 0
        assert (await uow.usage.summary(user_id=unique_id)).invocations == 0
        assert await uow.users.get(unique_id) is None


async def test_a_direct_message_conversation_is_not_shared_between_members(
    service_context: ServiceContext, unique_id: int
) -> None:
    conversations = ConversationService(service_context)
    first = await conversations.current(guild_id=None, channel_id=None, user_id=unique_id)
    second = await conversations.current(guild_id=None, channel_id=None, user_id=unique_id + 1)
    assert first.id != second.id
    assert first.scope is ConversationScope.USER


async def test_usage_events_are_only_counted_for_their_own_scope(
    service_context: ServiceContext, unique_id: int
) -> None:
    async with service_context.unit_of_work() as uow:
        await uow.usage.append(
            command="ask",
            surface=UsageSurface.SLASH_COMMAND,
            guild_id=unique_id,
            user_id=unique_id,
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=100,
        )
        await uow.usage.append(
            command="ask",
            surface=UsageSurface.SLASH_COMMAND,
            guild_id=unique_id + 1,
            user_id=unique_id + 1,
            prompt_tokens=1000,
            completion_tokens=500,
            latency_ms=200,
        )

    async with service_context.unit_of_work() as uow:
        mine = await uow.usage.summary(guild_id=unique_id)
        theirs = await uow.usage.summary(user_id=unique_id + 1)
        everything = await uow.usage.summary()
    assert (mine.prompt_tokens, mine.completion_tokens) == (10, 5)
    assert (theirs.prompt_tokens, theirs.completion_tokens) == (1000, 500)
    assert everything.invocations >= 2


async def test_recorded_usage_reaches_the_daily_series(
    service_context: ServiceContext, unique_id: int
) -> None:
    from the_sun.services import UsageService

    usage = UsageService(service_context)
    await usage.record(
        UsageRecord(
            command="ask",
            surface=UsageSurface.SLASH_COMMAND,
            guild_id=unique_id,
            user_id=unique_id,
            provider="recorded",
            model="recorded-model",
            prompt_tokens=7,
            completion_tokens=3,
            latency_ms=12,
        )
    )
    series = await usage.daily_series(guild_id=unique_id, limit=5)
    assert series, "the event must appear in the per-day aggregation"
    assert series[0].invocations >= 1
    assert series[0].prompt_tokens >= 7
    breakdown = await usage.provider_breakdown(guild_id=unique_id, limit=5)
    assert any(
        entry.provider == "recorded" and entry.model == "recorded-model" for entry in breakdown
    )
