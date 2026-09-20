"""Logging configuration.

Two rules matter here:

* every log line can carry a correlation id, so a Discord interaction can be
  traced through the service, repository and provider layers;
* credentials never reach a log. Values whose key looks secret are redacted
  structurally, and the rendered line is scrubbed for the credential shapes this
  project actually handles (``Bot <token>``, ``Bearer <token>``, URL passwords).
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "REDACTED",
    "bind_correlation_id",
    "clear_correlation_id",
    "configure_logging",
    "current_correlation_id",
    "new_correlation_id",
    "redact_text",
]

REDACTED = "<redacted>"

_SECRET_KEY_PATTERN = re.compile(
    r"(authorization|api[-_]?key|secret|password|passwd|token|credential)", re.IGNORECASE
)
_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Bot\s+)[A-Za-z0-9_.\-]{8,}"), r"\1" + REDACTED),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_.\-]{8,}"), r"\1" + REDACTED),
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^:/\s@]+:)[^@/\s]+(@)"), r"\1" + REDACTED + r"\2"),
)

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "the_sun_correlation_id", default=None
)


def new_correlation_id() -> str:
    """Generate a short, unique correlation id."""
    return uuid.uuid4().hex[:12]


def bind_correlation_id(correlation_id: str | None = None) -> contextvars.Token[str | None]:
    """Bind a correlation id to the current context; returns a reset token."""
    return _correlation_id.set(correlation_id or new_correlation_id())


def clear_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Restore the correlation id that was bound before ``token``."""
    _correlation_id.reset(token)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def redact_text(value: str) -> str:
    """Scrub credential shapes from a free-form string."""
    redacted = value
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            redacted[key] = _redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            redacted[key] = [
                _redact_mapping(item) if isinstance(item, dict) else item for item in value
            ]
        elif isinstance(value, str):
            redacted[key] = REDACTED if _SECRET_KEY_PATTERN.search(key) else redact_text(value)
        else:
            redacted[key] = value
    return redacted


_RESERVED_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        correlation_id = current_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_KEYS and not key.startswith("_")
        }
        if extras:
            payload["extra"] = _redact_mapping(extras)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable output for local development."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        correlation_id = current_correlation_id()
        if correlation_id:
            rendered = f"{rendered} [cid={correlation_id}]"
        return redact_text(rendered)


def _resolve_level(level: str) -> int:
    """Translate a configured level name, degrading to INFO when it is unknown."""
    resolved = logging.getLevelNamesMapping().get(level.strip().upper())
    if resolved is None:
        logging.getLogger(__name__).warning(
            "unknown log level, falling back to INFO", extra={"configured_level": level}
        )
        return logging.INFO
    return int(resolved)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install the root handler. Safe to call more than once."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(_resolve_level(level))

    # discord.py is noisy at INFO for gateway chatter; keep it at WARNING and
    # raise our own namespaces back to the configured level.
    logging.getLogger("discord").setLevel(max(logging.WARNING, root.level))
    logging.getLogger("the_sun").setLevel(root.level)
