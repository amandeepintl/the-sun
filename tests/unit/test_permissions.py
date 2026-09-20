"""Permission and channel allow-list rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from the_sun.errors import PermissionDeniedError
from the_sun.services import CallerContext, GuildSettingsView, PermissionService
from the_sun.services.permissions import is_guild_admin


def _settings(**overrides: object) -> GuildSettingsView:
    values: dict[str, object] = {
        "guild_id": 111,
        "ai_provider": None,
        "ai_model": None,
        "system_prompt": None,
        "history_length": 20,
        "memory_enabled": True,
        "ephemeral_responses": True,
        "rate_limit_per_user_per_minute": None,
        "rate_limit_per_guild_per_minute": None,
        "daily_token_quota": None,
        "admin_role_ids": [],
        "allowed_channel_ids": [],
        "history_retention_days": None,
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return GuildSettingsView(**values)  # type: ignore[arg-type]


def _caller(**overrides: object) -> CallerContext:
    values: dict[str, object] = {
        "user_id": 7,
        "guild_id": 111,
        "channel_id": 222,
        "role_ids": frozenset(),
        "has_manage_guild": False,
        "is_guild_owner": False,
    }
    values.update(overrides)
    return CallerContext(**values)  # type: ignore[arg-type]


def test_manage_server_grants_administration() -> None:
    assert is_guild_admin(_caller(has_manage_guild=True), _settings()) is True


def test_guild_owner_grants_administration() -> None:
    assert is_guild_admin(_caller(is_guild_owner=True), _settings()) is True


def test_configured_admin_role_grants_administration() -> None:
    settings = _settings(admin_role_ids=[333])
    assert is_guild_admin(_caller(role_ids=frozenset({333, 444})), settings) is True
    assert is_guild_admin(_caller(role_ids=frozenset({444})), settings) is False


def test_plain_member_is_not_an_administrator() -> None:
    assert is_guild_admin(_caller(), _settings()) is False
    assert is_guild_admin(_caller(), None) is False


def test_stale_admin_roles_are_reported() -> None:
    settings = _settings(admin_role_ids=[1, 2, 3])
    assert PermissionService.stale_admin_roles(settings, [1, 2]) == [3]


def test_channel_allow_list_permits_admins_choice() -> None:
    settings = _settings(allowed_channel_ids=[222])
    PermissionService.ensure_channel_allowed(_caller(channel_id=222), settings)


def test_channel_outside_the_allow_list_is_refused() -> None:
    settings = _settings(allowed_channel_ids=[222])
    with pytest.raises(PermissionDeniedError):
        PermissionService.ensure_channel_allowed(_caller(channel_id=999), settings)


def test_empty_allow_list_permits_every_channel() -> None:
    PermissionService.ensure_channel_allowed(_caller(channel_id=999), _settings())


def test_direct_messages_bypass_the_channel_allow_list() -> None:
    settings = _settings(allowed_channel_ids=[222])
    PermissionService.ensure_channel_allowed(_caller(guild_id=None, channel_id=999), settings)


def test_non_admin_cannot_change_guild_settings() -> None:
    with pytest.raises(PermissionDeniedError) as error:
        PermissionService.ensure_admin(_caller(), _settings())
    assert "Manage Server" in error.value.public_message


def test_settings_commands_are_refused_in_direct_messages() -> None:
    with pytest.raises(PermissionDeniedError):
        PermissionService.ensure_admin(_caller(guild_id=None), None)


def test_administrators_pass_the_settings_check() -> None:
    PermissionService.ensure_admin(_caller(has_manage_guild=True), _settings())


def test_ephemeral_default_follows_guild_settings() -> None:
    assert PermissionService.ephemeral_for(_settings(ephemeral_responses=True)) is True
    assert PermissionService.ephemeral_for(_settings(ephemeral_responses=False)) is False
    assert (
        PermissionService.ephemeral_for(_settings(ephemeral_responses=False), force_public=True)
        is False
    )
