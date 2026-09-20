"""Startup validation.

``validate_settings`` reports every problem it can find in one pass so an
operator can fix the whole ``.env`` at once instead of one failed start at a
time. It performs no network calls; reachability is the job of the health
service behind ``python -m the_sun doctor``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from the_sun.errors import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance for type checkers only
    from the_sun.config.settings import Settings

__all__ = ["require_valid_settings", "validate_settings"]

_KNOWN_LOG_LEVELS = frozenset(logging.getLevelNamesMapping())


def validate_settings(settings: Settings) -> list[str]:
    """Return a list of human-readable problems; empty means usable."""
    problems: list[str] = []

    if not settings.discord_token_value.strip():
        problems.append("DISCORD_TOKEN is empty")

    log_level = settings.log_level.strip().upper()
    if log_level not in _KNOWN_LOG_LEVELS:
        problems.append(f"LOG_LEVEL {settings.log_level!r} is not a known logging level")

    prefix = settings.redis_key_prefix.strip()
    if not prefix or any(character.isspace() for character in prefix):
        problems.append("REDIS_KEY_PREFIX must be a non-empty token without whitespace")

    for label, builder in (
        ("DATABASE_URL", lambda: settings.database),
        ("REDIS_URL", lambda: settings.cache),
        ("AI_PROVIDERS", lambda: settings.ai),
    ):
        try:
            builder()
        except ConfigurationError as exc:
            problems.append(str(exc))
        except Exception as exc:
            problems.append(f"{label} could not be resolved: {exc}")

    if not problems:
        try:
            ai = settings.ai
        except ConfigurationError as exc:
            problems.append(str(exc))
        else:
            # A provider's own timeout wins; AI_REQUEST_TIMEOUT_SECONDS is the
            # fallback for providers that do not set one, so there is nothing to
            # cross-check here beyond the bounds ProviderConfig already enforces.
            for name, provider in sorted(ai.providers.items()):
                if provider.timeout_seconds is None and ai.request_timeout_seconds <= 0:
                    problems.append(
                        f"provider {name!r} has no timeout_seconds and "
                        "AI_REQUEST_TIMEOUT_SECONDS is not positive"
                    )

    return problems


def require_valid_settings(settings: Settings) -> None:
    """Raise :class:`ConfigurationError` listing everything that is wrong."""
    problems = validate_settings(settings)
    if problems:
        detail = "\n".join(f"  - {problem}" for problem in problems)
        raise ConfigurationError(f"configuration is not usable:\n{detail}")
