"""Permission and channel rules.

The rules are pure functions over values Discord already gave us - whether the
caller holds Manage Server, which role ids they have, what an administrator
configured for this guild - which keeps them easy to reason about and to test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from the_sun.errors import PermissionDeniedError
from the_sun.services.settings_service import GuildSettingsView

__all__ = ["CallerContext", "PermissionService", "is_guild_admin"]


@dataclass(frozen=True, slots=True)
class CallerContext:
    """The bits of a Discord interaction the permission rules care about."""

    user_id: int
    guild_id: int | None
    channel_id: int | None
    role_ids: frozenset[int] = frozenset()
    has_manage_guild: bool = False
    is_guild_owner: bool = False


def is_guild_admin(caller: CallerContext, settings: GuildSettingsView | None) -> bool:
    """Whether a caller may administer The Sun in this guild.

    Manage Server counts, as does owning the guild. A guild may additionally
    delegate administration to specific roles, which is stored per guild and empty
    by default.
    """
    if caller.has_manage_guild or caller.is_guild_owner:
        return True
    if settings is None or not settings.admin_role_ids:
        return False
    return not caller.role_ids.isdisjoint(settings.admin_role_ids)


class PermissionService:
    """Channel and administration checks shared by every command."""

    @staticmethod
    def ensure_channel_allowed(caller: CallerContext, settings: GuildSettingsView) -> None:
        """Refuse commands in channels an administrator excluded."""
        if caller.guild_id is None:
            return  # direct messages are always allowed
        if not settings.is_channel_allowed(caller.channel_id):
            raise PermissionDeniedError(
                "this command is not enabled in this channel",
                public_message="The Sun is not enabled in this channel.",
            )

    @staticmethod
    def ensure_admin(caller: CallerContext, settings: GuildSettingsView | None) -> None:
        """Refuse administration commands from non-administrators."""
        if caller.guild_id is None:
            raise PermissionDeniedError(
                "guild settings are unavailable in direct messages",
                public_message="Server settings can only be changed inside a server.",
            )
        if not is_guild_admin(caller, settings):
            raise PermissionDeniedError(
                "the caller is not an administrator",
                public_message=(
                    "You need Manage Server permission (or an administrative role) to do that."
                ),
            )

    @staticmethod
    def ephemeral_for(settings: GuildSettingsView, *, force_public: bool = False) -> bool:
        """Whether a reply should default to being private to the caller."""
        if force_public:
            return False
        return settings.ephemeral_responses

    @staticmethod
    def stale_admin_roles(settings: GuildSettingsView, role_ids: Sequence[int]) -> list[int]:
        """Role ids referenced by a guild's configuration that no longer exist."""
        known = set(role_ids)
        return sorted(role for role in settings.admin_role_ids if role not in known)
