"""Architectural invariants, enforced by scanning the source tree.

These tests are the mechanism behind the project rules: no hardcoded identifiers,
secrets or model names; no SQL outside repositories; no environment access outside
config; no cache or Discord imports in the wrong layer; no test double reachable
from production code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "the_sun"

#: Every Python module in the shipped package.
SOURCE_FILES = sorted(SOURCE_ROOT.rglob("*.py"))

SNOWFLAKE_PATTERN = re.compile(r"\b\d{17,20}\b")
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "provider API key": re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    "slack-style token": re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    "discord bot token": re.compile(r"Bot\s+[A-Za-z0-9_.\-]{20,}"),
    "URL with an inline password": re.compile(r"[a-z][a-z0-9+.\-]*://[^:/\s@]+:[^@/\s]+@"),
}
MODEL_NAME_PATTERN = re.compile(
    r"\b(gpt-[0-9a-z]|claude-[0-9a-z]|gemini-[0-9a-z]|llama[0-9]|qwen[0-9]|mistral-|mixtral-|o[13]-mini)",
    re.IGNORECASE,
)

REQUIRED_PACKAGES = (
    "config",
    "db",
    "repositories",
    "cache",
    "ai",
    "services",
    "observability",
    "integrations",
)


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _iter_lines() -> list[tuple[Path, int, str]]:
    lines: list[tuple[Path, int, str]] = []
    for path in SOURCE_FILES:
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            lines.append((path, number, text))
    return lines


def test_source_tree_is_not_empty() -> None:
    assert SOURCE_FILES, "no source files were found"


def test_every_layer_package_exists() -> None:
    missing = [
        package
        for package in REQUIRED_PACKAGES
        if not (SOURCE_ROOT / package / "__init__.py").exists()
    ]
    assert missing == []


def test_no_discord_identifiers_are_hardcoded() -> None:
    offences = [
        f"{_relative(path)}:{number}: {text.strip()}"
        for path, number, text in _iter_lines()
        if SNOWFLAKE_PATTERN.search(text)
    ]
    assert offences == [], "Discord snowflakes must come from Discord, not source code"


@pytest.mark.parametrize("label", sorted(SECRET_PATTERNS))
def test_no_credentials_are_embedded_in_source(label: str) -> None:
    pattern = SECRET_PATTERNS[label]
    offences = [
        f"{_relative(path)}:{number}"
        for path, number, text in _iter_lines()
        if pattern.search(text)
    ]
    assert offences == [], f"found a {label} in source code: {offences}"


def test_no_model_names_are_hardcoded() -> None:
    offences = [
        f"{_relative(path)}:{number}: {text.strip()}"
        for path, number, text in _iter_lines()
        if MODEL_NAME_PATTERN.search(text)
    ]
    assert offences == [], "model identifiers must come from AI_PROVIDERS or the provider API"


def test_the_environment_is_only_read_by_the_config_package() -> None:
    offences = []
    for path, number, text in _iter_lines():
        reads_environment = any(
            marker in text for marker in ("os.environ", "os.getenv", "environ.get")
        )
        if reads_environment and path.parent.name != "config":
            offences.append(f"{_relative(path)}:{number}: {text.strip()}")
    assert offences == []


def test_redis_is_only_imported_by_the_cache_layer() -> None:
    offences = []
    for path, number, text in _iter_lines():
        if re.match(r"\s*(from|import)\s+redis\b", text) and path.parent.name != "cache":
            offences.append(f"{_relative(path)}:{number}: {text.strip()}")
    assert offences == []


def test_discord_is_only_imported_by_the_presentation_layer() -> None:
    allowed = {"bot.py", "__main__.py"}
    offences = []
    for path, number, text in _iter_lines():
        imports_discord = bool(re.match(r"\s*(from|import)\s+discord\b", text))
        in_presentation_layer = path.parent.name == "cogs" or path.name in allowed
        if imports_discord and not in_presentation_layer:
            offences.append(f"{_relative(path)}:{number}: {text.strip()}")
    assert offences == []


def test_sql_text_is_only_used_by_repositories_and_migrations() -> None:
    offences = []
    for path, number, text in _iter_lines():
        uses_raw_sql = bool(
            re.search(r"\btext\(\s*[\"'](SELECT|SHOW|INSERT|UPDATE|DELETE|WITH)", text)
        )
        in_database_layer = (
            path.parent.name in {"repositories", "migrations"} or path.name == "env.py"
        )
        if uses_raw_sql and not in_database_layer:
            offences.append(f"{_relative(path)}:{number}: {text.strip()}")
    assert offences == []


def test_no_canned_ai_responses_are_stored_in_source() -> None:
    suspicious = ("As an AI", "as an AI language model", "Sure! Here", "I'm sorry, I cannot")
    offences = [
        f"{_relative(path)}:{number}"
        for path, number, text in _iter_lines()
        if any(fragment in text for fragment in suspicious)
    ]
    assert offences == []


def test_production_code_never_imports_the_test_suite() -> None:
    offences = [
        f"{_relative(path)}:{number}: {text.strip()}"
        for path, number, text in _iter_lines()
        if re.match(r"\s*(from|import)\s+tests\b", text)
    ]
    assert offences == []


def test_guild_settings_defaults_are_declared_in_the_schema_not_in_code() -> None:
    """A guild's configuration must come from its row, not from a code literal."""
    text = (SOURCE_ROOT / "db" / "models" / "guild_settings.py").read_text(encoding="utf-8")
    assert "server_default" in text
    assert not SNOWFLAKE_PATTERN.search(text)


def test_metadata_declares_exactly_the_designed_tables() -> None:
    from the_sun.db.models import Base

    assert sorted(Base.metadata.tables) == [
        "conversations",
        "guild_settings",
        "memories",
        "messages",
        "usage_events",
        "users",
    ]


def test_dynamic_data_policy_is_documented() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Nothing dynamic is hardcoded" in readme
    assert ".env.example" in readme
