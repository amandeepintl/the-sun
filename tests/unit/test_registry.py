"""Provider registry resolution, fallbacks and lifecycle."""

from __future__ import annotations

import json

import pytest

from the_sun.ai import ADAPTER_TYPES, OpenAICompatibleProvider, create_registry
from the_sun.config import Settings
from the_sun.errors import ConfigurationError, ProviderNotConfiguredError


def _settings(**overrides: object) -> Settings:
    document = {
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
    values: dict[str, object] = {
        "_env_file": None,
        "DISCORD_TOKEN": "unit-test-discord-token",
        "DATABASE_URL": "postgresql://unit:unit@localhost:5432/unit",
        "REDIS_URL": "redis://localhost:6379/0",
        "AI_PROVIDERS": json.dumps(document),
        "DEFAULT_PROVIDER": "primary",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_registry_exposes_every_configured_provider() -> None:
    registry = create_registry(_settings().ai)
    assert registry.names() == ("primary", "secondary")
    assert registry.default_name == "primary"


def test_default_provider_is_used_when_none_is_requested() -> None:
    registry = create_registry(_settings().ai)
    provider = registry.get()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "primary"
    assert provider.default_model == "primary-model"


def test_provider_instances_are_cached() -> None:
    registry = create_registry(_settings().ai)
    assert registry.get("primary") is registry.get("primary")


def test_unknown_provider_raises() -> None:
    registry = create_registry(_settings().ai)
    with pytest.raises(ProviderNotConfiguredError) as error:
        registry.get("ghost")
    assert "primary" in str(error.value)


def test_guild_override_selects_that_provider() -> None:
    registry = create_registry(_settings().ai)
    assert registry.resolve(guild_provider="secondary").name == "secondary"


def test_guild_override_that_no_longer_exists_falls_back_to_the_default() -> None:
    registry = create_registry(_settings().ai)
    assert registry.resolve(guild_provider="removed-provider").name == "primary"


def test_describe_never_exposes_credentials() -> None:
    registry = create_registry(_settings().ai)
    rendered = json.dumps(registry.describe())
    assert "primary-key" not in rendered
    assert '"authenticated": true' in rendered


def test_unregistered_adapter_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(ADAPTER_TYPES, "openai_compatible")
    registry = create_registry(_settings().ai)
    with pytest.raises(ConfigurationError) as error:
        registry.get("primary")
    assert "not registered" in str(error.value)


def test_openai_compatible_adapter_is_registered() -> None:
    assert ADAPTER_TYPES["openai_compatible"] is OpenAICompatibleProvider


async def test_aclose_releases_provider_clients_and_instances() -> None:
    registry = create_registry(_settings().ai)
    first = registry.get("primary")
    assert isinstance(first, OpenAICompatibleProvider)
    assert first.client is not None  # force creation of the httpx client
    await registry.aclose()
    assert registry.get("primary") is not first
