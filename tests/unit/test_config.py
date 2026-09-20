"""Settings parsing, normalisation and validation."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError as PydanticValidationError
from tests.conftest import unit_provider_document

from the_sun.config import Settings, get_settings, reset_settings_cache, validate_settings
from the_sun.errors import ConfigurationError


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DISCORD_TOKEN": "unit-test-discord-token",
        "DATABASE_URL": "postgresql://user:secret@db.example.com:5432/sun",
        "REDIS_URL": "redis://:redis-secret@cache.example.com:6379/0",
        "AI_PROVIDERS": unit_provider_document(),
        "DEFAULT_PROVIDER": "primary",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_settings_are_read_from_the_environment() -> None:
    settings = _settings()
    assert settings.default_provider == "primary"
    assert settings.app_env == "development"
    assert settings.discord_token_description.startswith("<set")


def test_database_url_is_normalised_to_asyncpg() -> None:
    settings = _settings()
    assert settings.database.url.startswith("postgresql+asyncpg://")
    assert settings.database.safe_url.startswith("postgresql+asyncpg://")
    assert "secret" not in settings.database.safe_url


def test_cache_url_password_is_masked() -> None:
    assert "redis-secret" not in _settings().cache.safe_url


def test_non_postgres_database_url_is_rejected() -> None:
    settings = _settings(DATABASE_URL="sqlite+aiosqlite:///local.db")
    with pytest.raises(ConfigurationError):
        _ = settings.database


def test_invalid_database_url_is_rejected() -> None:
    settings = _settings(DATABASE_URL="not a url")
    with pytest.raises(ConfigurationError):
        _ = settings.database


def test_non_redis_cache_url_is_rejected() -> None:
    settings = _settings(REDIS_URL="http://cache.example.com")
    with pytest.raises(ConfigurationError):
        _ = settings.cache


def test_test_database_prefers_the_dedicated_variable() -> None:
    settings = _settings(TEST_DATABASE_URL="postgresql://user:secret@localhost:5432/scratch")
    assert "scratch" in settings.test_database().url
    without_override = _settings()
    assert without_override.test_database().url == without_override.database.url


def test_missing_required_variables_are_reported_by_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CI exports these variables for the integration job, so the test must
    # remove them to see the "everything missing" error it is asserting on.
    for variable in ("DISCORD_TOKEN", "DATABASE_URL", "REDIS_URL", "AI_PROVIDERS"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(PydanticValidationError) as error:
        Settings(_env_file=None)
    reported = {str(entry["loc"][0]) for entry in error.value.errors()}
    assert {"DISCORD_TOKEN", "DATABASE_URL", "REDIS_URL", "AI_PROVIDERS"} <= reported


def test_validate_settings_collects_every_problem() -> None:
    settings = _settings(
        DISCORD_TOKEN="   ",
        LOG_LEVEL="CHATTY",
        REDIS_KEY_PREFIX="has space",
        AI_PROVIDERS="{not json",
    )
    problems = validate_settings(settings)
    assert any("DISCORD_TOKEN" in problem for problem in problems)
    assert any("LOG_LEVEL" in problem for problem in problems)
    assert any("REDIS_KEY_PREFIX" in problem for problem in problems)
    assert any("AI_PROVIDERS" in problem for problem in problems)
    assert len(problems) >= 4


def test_validate_settings_accepts_a_complete_configuration() -> None:
    assert validate_settings(_settings()) == []


def test_validate_settings_reports_unknown_default_provider() -> None:
    problems = validate_settings(_settings(DEFAULT_PROVIDER="missing"))
    assert any("DEFAULT_PROVIDER" in problem for problem in problems)


def test_a_provider_timeout_wins_over_the_deployment_default() -> None:
    """AI_REQUEST_TIMEOUT_SECONDS is the fallback, so a provider value is accepted."""
    settings = _settings(
        AI_PROVIDERS=unit_provider_document(timeout_seconds=120),
        AI_REQUEST_TIMEOUT_SECONDS=30,
    )
    assert validate_settings(settings) == []
    assert settings.ai.effective_timeout(settings.ai.provider(None)) == 120
    resolved = settings.ai.with_resolved_timeout(settings.ai.provider(None))
    assert resolved.timeout_seconds == 120


def test_the_deployment_default_fills_in_a_provider_without_a_timeout() -> None:
    settings = _settings(AI_REQUEST_TIMEOUT_SECONDS=45)
    provider = settings.ai.provider(None)
    assert provider.timeout_seconds is None
    assert settings.ai.effective_timeout(provider) == 45
    assert settings.ai.with_resolved_timeout(provider).timeout_seconds == 45


def test_an_absurd_provider_timeout_is_rejected() -> None:
    problems = validate_settings(
        _settings(AI_PROVIDERS=unit_provider_document(timeout_seconds=5000))
    )
    assert any("timeout_seconds" in problem for problem in problems)


def test_provider_document_must_be_json() -> None:
    problems = validate_settings(_settings(AI_PROVIDERS=json.dumps("primary")))
    assert any("non-empty JSON object" in problem for problem in problems)


def test_get_settings_is_cached_and_resettable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token-from-environment")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost:5432/sun")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("AI_PROVIDERS", unit_provider_document())
    monkeypatch.setenv("DEFAULT_PROVIDER", "primary")
    reset_settings_cache()
    first = get_settings()
    assert get_settings() is first
    reset_settings_cache()
    assert get_settings() is not first
