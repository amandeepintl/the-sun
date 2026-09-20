"""The one sanctioned raw Discord REST call.

All conversational Discord traffic goes through ``discord.py`` in the
presentation layer. The single exception is this read-only identity probe, used by
``python -m the_sun doctor`` to prove that the configured token is valid and to
report which bot account it belongs to *before* the gateway client exists.

It performs exactly one request, reads only the bot's own identity, and changes
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from the_sun.errors import ConfigurationError

__all__ = [
    "DISCORD_API_BASE",
    "DiscordApplication",
    "DiscordIdentity",
    "fetch_application",
    "fetch_bot_identity",
]

#: Versioned REST base for Discord's API.
DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass(frozen=True, slots=True)
class DiscordApplication:
    """Identity of the application a token belongs to.

    Read from the API so the install URL is built from the real application id and
    never from a value pasted into the source.
    """

    id: int
    name: str
    description: str | None = None
    bot_public: bool = False

    @property
    def install_url(self) -> str:
        return f"https://discord.com/oauth2/authorize?client_id={self.id}"


@dataclass(frozen=True, slots=True)
class DiscordIdentity:
    """Identity of the bot account behind a token."""

    id: int
    username: str
    discriminator: str | None
    bot: bool
    global_name: str | None = None

    @property
    def label(self) -> str:
        if self.discriminator and self.discriminator != "0":
            return f"{self.username}#{self.discriminator}"
        return self.username


async def fetch_application(token: str, *, timeout_seconds: float = 10.0) -> DiscordApplication:
    """Fetch ``GET /applications/@me`` for the configured bot token."""
    document = await _get(token, "/applications/@me", timeout_seconds=timeout_seconds)
    application_id = _as_int(document.get("id"))
    if application_id is None:
        raise ConfigurationError("the Discord API returned an unexpected application payload")
    return DiscordApplication(
        id=application_id,
        name=_as_text(document.get("name")) or "",
        description=_as_text(document.get("description")),
        bot_public=bool(document.get("bot_public", False)),
    )


async def fetch_bot_identity(token: str, *, timeout_seconds: float = 10.0) -> DiscordIdentity:
    """Fetch ``GET /users/@me`` for the configured bot token."""
    document = await _get(token, "/users/@me", timeout_seconds=timeout_seconds)
    user_id = _as_int(document.get("id"))
    if user_id is None:
        raise ConfigurationError("the Discord API returned an unexpected identity payload")
    return DiscordIdentity(
        id=user_id,
        username=_as_text(document.get("username")) or "",
        discriminator=_as_text(document.get("discriminator")),
        bot=bool(document.get("bot", False)),
        global_name=_as_text(document.get("global_name")),
    )


def _as_int(value: object) -> int | None:
    """Read an identifier from an API document, accepting numeric strings too."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _get(token: str, path: str, *, timeout_seconds: float) -> dict[str, object]:
    """Perform one authenticated read against the Discord REST API."""
    if not token.strip():
        raise ConfigurationError("DISCORD_TOKEN is empty")
    headers = {"Authorization": f"Bot {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(
            base_url=DISCORD_API_BASE, timeout=httpx.Timeout(timeout_seconds, connect=5.0)
        ) as client:
            response = await client.get(path, headers=headers)
    except httpx.HTTPError as exc:
        raise ConfigurationError(f"could not reach the Discord API: {exc}") from exc

    if response.status_code == 401:
        raise ConfigurationError("the Discord API rejected DISCORD_TOKEN (401 Unauthorized)")
    if response.status_code >= 400:
        raise ConfigurationError(f"the Discord API returned {response.status_code} for {path}")
    document = response.json()
    if not isinstance(document, dict):
        raise ConfigurationError("the Discord API returned an unexpected payload")
    return document
