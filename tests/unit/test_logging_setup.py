"""Logging: credential redaction and correlation ids."""

from __future__ import annotations

import json
import logging

from the_sun.logging_setup import (
    REDACTED,
    JsonFormatter,
    bind_correlation_id,
    clear_correlation_id,
    configure_logging,
    current_correlation_id,
    new_correlation_id,
    redact_text,
)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="the_sun.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_redact_text_removes_discord_bot_tokens() -> None:
    rendered = redact_text("Authorization: Bot " + "A" * 30)
    assert "A" * 30 not in rendered
    assert REDACTED in rendered


def test_redact_text_removes_bearer_tokens() -> None:
    rendered = redact_text("Bearer sk-abcdefghijklmnopqrstuvwxyz")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in rendered


def test_redact_text_removes_url_passwords() -> None:
    rendered = redact_text("postgresql://user:super-secret@host:5432/db")
    assert "super-secret" not in rendered
    assert "user" in rendered


def test_json_formatter_redacts_secret_looking_fields() -> None:
    record = _record("provider configured", api_key="sk-live-secret", provider="primary")
    payload = json.loads(JsonFormatter().format(record))
    assert payload["extra"]["api_key"] == REDACTED
    assert payload["extra"]["provider"] == "primary"
    assert payload["message"] == "provider configured"


def test_json_formatter_redacts_nested_secrets() -> None:
    record = _record("nested", provider={"headers": {"authorization": "Bearer abcdefghij"}})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["extra"]["provider"]["headers"]["authorization"] == REDACTED


def test_json_formatter_includes_the_correlation_id() -> None:
    token = bind_correlation_id("correlation-123")
    try:
        payload = json.loads(JsonFormatter().format(_record("hello")))
    finally:
        clear_correlation_id(token)
    assert payload["correlation_id"] == "correlation-123"


def test_correlation_ids_are_unique_and_clearable() -> None:
    assert new_correlation_id() != new_correlation_id()
    token = bind_correlation_id()
    assert current_correlation_id() is not None
    clear_correlation_id(token)
    assert current_correlation_id() is None


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    configure_logging("DEBUG", json_output=True)
    configure_logging("WARNING", json_output=False)
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING
    assert logging.getLogger("the_sun").level == logging.WARNING


def test_json_formatter_includes_exception_text_without_leaking_tokens() -> None:
    try:
        raise RuntimeError("failed with Bot " + "B" * 25)
    except RuntimeError as error:
        record = _record("boom")
        record.exc_info = (type(error), error, error.__traceback__)
    payload = json.loads(JsonFormatter().format(record))
    assert "B" * 25 not in payload["exception"]
