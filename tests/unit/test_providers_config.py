"""AI_PROVIDERS parsing, validation and credential resolution."""

from __future__ import annotations

import json

import pytest

from the_sun.config import ProviderConfig, parse_provider_configs
from the_sun.errors import ConfigurationError


def _document(**provider: object) -> str:
    entry: dict[str, object] = {
        "type": "openai_compatible",
        "base_url": "https://provider.invalid/v1",
        "default_model": "unit-test-model",
    }
    entry.update(provider)
    return json.dumps({"primary": entry})


def test_parses_a_provider_document() -> None:
    providers = parse_provider_configs(_document(), default_provider="primary")
    provider = providers["primary"]
    assert provider.name == "primary"
    assert provider.type == "openai_compatible"
    assert provider.default_model == "unit-test-model"
    assert provider.api_key is None


def test_rejects_empty_document() -> None:
    with pytest.raises(ConfigurationError):
        parse_provider_configs("", default_provider="primary")


def test_rejects_invalid_json() -> None:
    with pytest.raises(ConfigurationError) as error:
        parse_provider_configs("{oops", default_provider="primary")
    assert "not valid JSON" in str(error.value)


def test_rejects_non_object_document() -> None:
    with pytest.raises(ConfigurationError):
        parse_provider_configs("[]", default_provider="primary")


def test_rejects_unknown_adapter_type() -> None:
    with pytest.raises(ConfigurationError) as error:
        parse_provider_configs(_document(type="telepathy"), default_provider="primary")
    assert "unsupported provider type" in str(error.value)


def test_rejects_relative_base_url() -> None:
    with pytest.raises(ConfigurationError):
        parse_provider_configs(_document(base_url="/v1"), default_provider="primary")


def test_rejects_unknown_default_provider() -> None:
    with pytest.raises(ConfigurationError) as error:
        parse_provider_configs(_document(), default_provider="secondary")
    assert "DEFAULT_PROVIDER" in str(error.value)


def test_rejects_unknown_provider_fields() -> None:
    with pytest.raises(ConfigurationError) as error:
        parse_provider_configs(_document(api_keyy="typo"), default_provider="primary")
    assert "extra_forbidden" in str(error.value) or "api_keyy" in str(error.value)


def test_api_key_env_is_resolved_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_PROVIDER_KEY", "resolved-secret")
    providers = parse_provider_configs(
        _document(api_key_env="EXAMPLE_PROVIDER_KEY"), default_provider="primary"
    )
    provider = providers["primary"]
    assert provider.api_key_value == "resolved-secret"
    assert provider.safe_summary()["api_key_source"] == "env:EXAMPLE_PROVIDER_KEY"
    assert "resolved-secret" not in json.dumps(provider.safe_summary())


def test_missing_api_key_env_variable_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABSENT_PROVIDER_KEY", raising=False)
    with pytest.raises(ConfigurationError) as error:
        parse_provider_configs(
            _document(api_key_env="ABSENT_PROVIDER_KEY"), default_provider="primary"
        )
    assert "ABSENT_PROVIDER_KEY" in str(error.value)


def test_inline_api_key_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXAMPLE_PROVIDER_KEY", "from-environment")
    providers = parse_provider_configs(
        _document(api_key="inline-secret", api_key_env="EXAMPLE_PROVIDER_KEY"),
        default_provider="primary",
    )
    assert providers["primary"].api_key_value == "inline-secret"


def test_keyless_provider_is_allowed() -> None:
    providers = parse_provider_configs(_document(), default_provider="primary")
    assert providers["primary"].api_key_value is None


def test_base_url_trailing_slash_is_trimmed() -> None:
    providers = parse_provider_configs(
        _document(base_url="https://provider.invalid/v1/"), default_provider="primary"
    )
    assert providers["primary"].base_url == "https://provider.invalid/v1"


def test_safe_summary_never_contains_the_credential() -> None:
    provider = ProviderConfig(
        name="primary",
        base_url="https://provider.invalid/v1",
        default_model="unit-test-model",
        api_key="top-secret-value",
    )
    summary = provider.safe_summary()
    assert "top-secret-value" not in json.dumps(summary)
    assert "api_key" not in summary, "no field may carry the credential itself"
    assert summary["authenticated"] is True
    assert summary["api_key_source"] == "inline"
