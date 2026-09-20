"""The command surface, answer delivery and message rendering.

The command tree is built by loading every real cog, so these tests fail if a
command is renamed, dropped or left unregistered. Rendering is checked against
real objects built from real inputs - never against sample output.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import discord
import pytest
from discord import app_commands

from the_sun.ai import Usage
from the_sun.bot import REQUIRED_BOT_PERMISSIONS, SunBot, build_intents
from the_sun.cogs.ask import history_embed
from the_sun.cogs.base import (
    EMPTY_ANSWER_NOTICE,
    MAX_MESSAGE_LENGTH,
    split_for_discord,
)
from the_sun.cogs.help import help_embed
from the_sun.cogs.settings_cog import DEFAULT_SENTINEL, RESET_CHANGES, SettingsCog
from the_sun.cogs.stats import stats_embed
from the_sun.cogs.status import status_embed
from the_sun.config import Settings
from the_sun.db.models import Message, MessageKind, MessageRole
from the_sun.repositories import (
    CommandUsage,
    DailyUsage,
    ProviderUsage,
    UsageSummary,
)
from the_sun.services import AIAnswer, CheckResult

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

#: Slash commands the bot must publish. Renaming one means updating this list.
EXPECTED_COMMANDS = {
    "ask",
    "new",
    "history",
    "summarize",
    "explain",
    "translate",
    "memory",
    "settings",
    "stats",
    "privacy",
    "status",
    "ping",
    "help",
    "invite",
}

#: Message context menus the bot must publish.
EXPECTED_MENUS = {
    "Ask The Sun",
    "Explain this message",
    "Translate this message",
    "Summarize from here",
    "Save to my memories",
}


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DISCORD_TOKEN": "unit-test-discord-token",
        "DATABASE_URL": "postgresql://unit:unit@localhost:5432/unit",
        "REDIS_URL": "redis://localhost:6379/0",
        "AI_PROVIDERS": json.dumps(
            {
                "primary": {
                    "type": "openai_compatible",
                    "base_url": "https://provider.invalid/v1",
                    "default_model": "unit-test-model",
                }
            }
        ),
        "DEFAULT_PROVIDER": "primary",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
async def bot() -> AsyncIterator[SunBot]:
    instance = SunBot(_settings())
    try:
        await instance.load_cogs()
        yield instance
    finally:
        await instance.service_context.aclose()


# --------------------------------------------------------------------------- #
# The published surface
# --------------------------------------------------------------------------- #
async def test_every_expected_command_is_published(bot: SunBot) -> None:
    names = {command.name for command in bot.tree.get_commands()}
    assert names >= EXPECTED_COMMANDS, f"missing: {sorted(EXPECTED_COMMANDS - names)}"


async def test_every_expected_context_menu_is_published(bot: SunBot) -> None:
    menus = {
        command.name
        for command in bot.tree.get_commands()
        if isinstance(command, app_commands.ContextMenu)
    }
    assert menus == EXPECTED_MENUS


async def test_command_descriptions_are_user_facing_text(bot: SunBot) -> None:
    for command in bot.tree.get_commands():
        if isinstance(command, app_commands.ContextMenu):
            continue
        assert command.description and len(command.description) <= 100
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                assert child.description, f"{command.name} {child.name} has no description"


async def test_admin_commands_require_manage_guild(bot: SunBot) -> None:
    commands = {command.name: command for command in bot.tree.get_commands()}
    settings_group = commands["settings"]
    assert isinstance(settings_group, app_commands.Group)
    assert settings_group.default_permissions is not None
    assert settings_group.default_permissions.manage_guild is True


async def test_help_lists_the_commands_that_really_exist(bot: SunBot) -> None:
    embed = help_embed(bot.tree.get_commands())
    rendered = "\n".join(f"{field.name}\n{field.value}" for field in embed.fields)
    for name in EXPECTED_COMMANDS - {"new", "ping"}:
        assert f"/{name}" in rendered, f"{name} is missing from help"
    for menu in EXPECTED_MENUS:
        assert menu in rendered
    assert "Message context menus" in rendered


async def test_loading_cogs_twice_is_refused_rather_than_duplicating(bot: SunBot) -> None:
    """A double load must fail loudly instead of registering commands twice."""
    before = len(bot.tree.get_commands())
    with pytest.raises(discord.ClientException):
        await bot.load_cogs()
    assert len(bot.tree.get_commands()) == before


async def test_services_are_shared_not_rebuilt(bot: SunBot) -> None:
    assert bot.ai_service.conversations is bot.conversation_service
    assert bot.memory_service is bot.ai_service.memories
    assert bot.ai_service.prompts is not None


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def test_short_answer_is_one_message() -> None:
    assert split_for_discord("a short answer") == ["a short answer"]


def test_empty_answer_is_reported_rather_than_sent_blank() -> None:
    assert split_for_discord("   \n ") == [EMPTY_ANSWER_NOTICE]


def test_long_answer_is_split_within_the_discord_limit() -> None:
    content = "\n\n".join(f"paragraph {index} " + "x" * 300 for index in range(20))
    chunks = split_for_discord(content)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_MESSAGE_LENGTH for chunk in chunks)
    joined = "".join(chunks)
    assert joined.count("paragraph") == 20, "no content may be lost"
    assert all(chunk.strip() for chunk in chunks)


def test_split_keeps_code_fences_balanced() -> None:
    body = "```python\n" + "\n".join(f"print({index})" for index in range(400)) + "\n```"
    chunks = split_for_discord(body)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0, "every chunk must have balanced fences"
        assert len(chunk) <= MAX_MESSAGE_LENGTH


def test_split_of_a_single_oversized_line_still_fits() -> None:
    chunks = split_for_discord("z" * (MAX_MESSAGE_LENGTH * 2 + 17))
    assert len(chunks) == 3
    assert all(len(chunk) <= MAX_MESSAGE_LENGTH for chunk in chunks)


def test_provenance_line_uses_reported_values() -> None:
    from the_sun.cogs.base import SunCog

    answer = AIAnswer(
        content="hello",
        provider="local",
        model="unit-test-model",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=1234,
        attempts=2,
        failover_used=True,
    )
    line = SunCog.provenance(answer)
    assert line.startswith("-# ")
    assert "provider local" in line
    assert "model unit-test-model" in line
    assert "15 tokens" in line
    assert "1234 ms" in line
    assert "failover used" in line


def test_provenance_omits_tokens_when_the_provider_reported_none() -> None:
    from the_sun.cogs.base import SunCog

    answer = AIAnswer(
        content="hello",
        provider="local",
        model="unit-test-model",
        usage=None,
        latency_ms=10,
        attempts=1,
        failover_used=False,
    )
    assert "tokens" not in SunCog.provenance(answer)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _field(embed: discord.Embed, name: str) -> str:
    """One embed field's text, with the optional type narrowed away."""
    value = next(field.value for field in embed.fields if field.name == name)
    assert value is not None
    return value


def _message(role: MessageRole, kind: MessageKind, content: str) -> Message:
    return Message(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role=role,
        kind=kind,
        content=content,
        created_at=NOW,
    )


def test_history_embed_shows_both_sides_and_the_kind() -> None:
    embed = history_embed(
        [
            _message(MessageRole.USER, MessageKind.CHAT, "what is redis?"),
            _message(MessageRole.ASSISTANT, MessageKind.EXPLAIN, "a data store"),
        ],
        page=1,
        total=2,
        name="ada",
    )
    names = [field.name for field in embed.fields]
    assert names[0] == "**You**"
    assert names[1] is not None and names[1].startswith("**The Sun**")
    assert names[1] is not None and "`explain`" in names[1]
    assert "what is redis?" in _field(embed, "**You**")
    assert embed.footer.text == "page 1 · 2 stored turns"


def test_history_embed_explains_an_empty_conversation() -> None:
    embed = history_embed([], page=1, total=0, name="ada")
    assert "Nothing has been stored" in (embed.description or "")


def test_stats_embed_reports_real_aggregates() -> None:
    embed = stats_embed(
        scope="server",
        window="week",
        summary=UsageSummary(
            invocations=12,
            successful=10,
            failed=1,
            rate_limited=1,
            prompt_tokens=1000,
            completion_tokens=500,
            average_latency_ms=812.4,
            first_occurred_at=NOW,
            last_occurred_at=NOW,
        ),
        top_commands=[CommandUsage(command="ask", invocations=9, failures=1)],
        providers=[
            ProviderUsage(
                provider="local",
                model="unit-test-model",
                invocations=12,
                prompt_tokens=1000,
                completion_tokens=500,
            )
        ],
        series=[DailyUsage(day=NOW, invocations=3, prompt_tokens=100, completion_tokens=50)],
    )
    assert "12 total" in _field(embed, "Requests")
    assert "10 succeeded · 1 failed · 1 rate limited" in _field(embed, "Requests")
    assert "1,000 in · 500 out · 1,500 total" in _field(embed, "Tokens (reported by providers)")
    assert "812 ms average" in _field(embed, "Latency")
    assert "`/ask` · 9 · 1 failed" in _field(embed, "Commands")
    assert "`local` / `unit-test-model` · 12 requests · 1,500 tokens" in _field(embed, "Providers")
    assert _field(embed, "Most recent day").startswith("2026-09-20")


def test_stats_embed_says_so_when_nothing_happened() -> None:
    embed = stats_embed(
        scope="your",
        window="day",
        summary=UsageSummary(
            invocations=0,
            successful=0,
            failed=0,
            rate_limited=0,
            prompt_tokens=0,
            completion_tokens=0,
            average_latency_ms=None,
            first_occurred_at=None,
            last_occurred_at=None,
        ),
        top_commands=[],
        providers=[],
        series=[],
    )
    assert "No requests have been recorded" in (embed.description or "")


def test_status_embed_marks_failing_checks_and_shows_circuits() -> None:
    embed = status_embed(
        checks=[
            CheckResult(name="database", ok=True, detail="PostgreSQL 16", latency_ms=4.2),
            CheckResult(name="cache", ok=False, detail="ConnectionRefused", latency_ms=1.0),
        ],
        providers=[{"provider": "primary", "state": "closed", "open_until": None}],
        settings=None,
        latency_ms=42.0,
    )
    assert "`database` · ok · 4 ms" in _field(embed, "Dependencies")
    assert "`cache` · failing" in _field(embed, "Dependencies")
    assert "`primary` · circuit `closed`" in _field(embed, "Providers")
    assert "42 ms" in (embed.footer.text or "")


# --------------------------------------------------------------------------- #
# Guards around the settings surface
# --------------------------------------------------------------------------- #
def test_reset_covers_every_updatable_column() -> None:
    from the_sun.repositories.guild_settings import UPDATABLE_FIELDS

    assert set(RESET_CHANGES) == set(UPDATABLE_FIELDS)


def test_model_sentinel_is_a_valid_choice_value() -> None:
    assert DEFAULT_SENTINEL.strip()
    assert DEFAULT_SENTINEL != "", "Discord rejects empty choice values"


async def test_settings_autocomplete_offers_configured_providers(bot: SunBot) -> None:
    """Autocomplete lists what is really configured, plus the default sentinel."""

    class StubInteraction:
        namespace = type("Namespace", (), {"provider": None})()

    cog = SettingsCog(bot)
    choices = await cog.provider_autocomplete(StubInteraction(), "")  # type: ignore[arg-type]
    assert choices[0].value == DEFAULT_SENTINEL
    assert {choice.value for choice in choices[1:]} == {"primary"}

    filtered = await cog.provider_autocomplete(StubInteraction(), "zzz")  # type: ignore[arg-type]
    assert len(filtered) == 1, "only the sentinel survives a non-matching filter"


def test_required_permissions_do_not_include_moderation() -> None:
    assert REQUIRED_BOT_PERMISSIONS.administrator is False
    assert REQUIRED_BOT_PERMISSIONS.manage_guild is False
    assert REQUIRED_BOT_PERMISSIONS.ban_members is False
    assert build_intents(_settings()).members is False
