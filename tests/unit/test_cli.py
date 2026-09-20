"""Command line behaviour.

The doctor failure case points at closed local ports, so the test proves that
failures are reported honestly without depending on, or contacting, any external
service.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import unit_provider_document

from the_sun.__main__ import EXIT_CHECK_FAILED, EXIT_CONFIG_ERROR, EXIT_OK, main


@pytest.fixture
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run the CLI away from any real .env, with explicit environment values."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "unit-test-discord-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unit:unit@127.0.0.1:1/unit")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("AI_PROVIDERS", unit_provider_document(base_url="https://127.0.0.1:1/v1"))
    monkeypatch.setenv("DEFAULT_PROVIDER", "primary")


def test_check_config_reports_a_usable_configuration(
    isolated_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-config"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "configuration is valid" in captured.out
    assert "default provider: primary" in captured.out


def test_check_config_json_output(
    isolated_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check-config", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["providers"] == ["primary"]


def test_check_config_reports_problems(
    isolated_environment: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_LEVEL", "CHATTY")
    assert main(["check-config"]) == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "LOG_LEVEL" in captured.err


def test_missing_environment_variables_are_explained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    for variable in (
        "DISCORD_TOKEN",
        "DATABASE_URL",
        "REDIS_URL",
        "AI_PROVIDERS",
        "DEFAULT_PROVIDER",
    ):
        monkeypatch.delenv(variable, raising=False)
    assert main(["check-config"]) == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "required environment variables" in captured.err


def test_doctor_reports_failing_dependencies(
    isolated_environment: None, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["doctor", "--json", "--skip-discord"])
    assert exit_code == EXIT_CHECK_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "database" in payload["failures"]
    assert "cache" in payload["failures"]
    assert "provider:primary" in payload["failures"]
    assert payload["checks"][0]["name"] == "config"
    assert payload["checks"][0]["ok"] is True


def test_help_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
