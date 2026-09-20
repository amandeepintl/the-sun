"""Configuration layer: settings, provider parsing and validation.

This is the only package allowed to read the process environment.
"""

from __future__ import annotations

from the_sun.config.prompts import DEFAULT_SYSTEM_PROMPT
from the_sun.config.providers import AIConfig, ProviderConfig, parse_provider_configs
from the_sun.config.settings import (
    CacheConfig,
    DatabaseConfig,
    Settings,
    get_settings,
    reset_settings_cache,
)
from the_sun.config.validation import require_valid_settings, validate_settings

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "AIConfig",
    "CacheConfig",
    "DatabaseConfig",
    "ProviderConfig",
    "Settings",
    "get_settings",
    "parse_provider_configs",
    "require_valid_settings",
    "reset_settings_cache",
    "validate_settings",
]
