"""Integrations with external systems that are not Discord's gateway.

See :mod:`the_sun.integrations.discord_rest` for why this package exists and why
it holds exactly one read-only Discord call.
"""

from __future__ import annotations

from the_sun.integrations.discord_rest import (
    DISCORD_API_BASE,
    DiscordApplication,
    DiscordIdentity,
    fetch_application,
    fetch_bot_identity,
)

__all__ = [
    "DISCORD_API_BASE",
    "DiscordApplication",
    "DiscordIdentity",
    "fetch_application",
    "fetch_bot_identity",
]
