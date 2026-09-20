"""Provider configuration models.

Provider instances are described entirely by the ``AI_PROVIDERS`` environment
variable, so adding, renaming or removing a provider never requires a code
change. Model identifiers are never written into source code: they arrive from
the environment or from the provider's live ``/models`` endpoint.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from the_sun.errors import ConfigurationError

__all__ = [
    "SUPPORTED_PROVIDER_TYPES",
    "AIConfig",
    "ProviderConfig",
    "parse_provider_configs",
]

#: Adapter implementations that exist today. A new adapter adds its identifier
#: here and one module implementing ``the_sun.ai.base.AIProvider``.
SUPPORTED_PROVIDER_TYPES: frozenset[str] = frozenset({"openai_compatible"})


def _lookup_env(name: str) -> str:
    """Resolve a variable name against the real process environment.

    ``os.environ`` alone is not enough: when the deployment uses a gitignored
    ``.env`` file, pydantic-settings loads it into the ``Settings`` fields but
    never exports those entries into ``os.environ``. Extra variables such as
    ``MYPROVIDER_API_KEY`` therefore have to be read from the same ``.env`` file
    the settings were loaded from. The file is consulted once and its entries
    never override variables that really exist in the environment.
    """
    value = os.environ.get(name, "")
    if value.strip():
        return value
    env_file = os.environ.get("THE_SUN_ENV_FILE", ".env")
    if not env_file or not Path(env_file).is_file():
        return ""
    try:
        entries = dotenv_values(env_file)
    except Exception:
        return ""
    resolved = entries.get(name)
    return resolved if isinstance(resolved, str) else ""


class ProviderConfig(BaseModel):
    """A single configured AI provider instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str = "openai_compatible"
    base_url: str
    default_model: str
    api_key: SecretStr | None = None
    api_key_env: str | None = None
    #: Per-provider request timeout. Unset means "use AI_REQUEST_TIMEOUT_SECONDS",
    #: which the registry resolves when it builds the adapter, so an adapter always
    #: has a concrete timeout to enforce.
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    extra_headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        return value.rstrip("/")

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        if value not in SUPPORTED_PROVIDER_TYPES:
            supported = ", ".join(sorted(SUPPORTED_PROVIDER_TYPES))
            raise ValueError(f"unsupported provider type {value!r} (supported: {supported})")
        return value

    @model_validator(mode="after")
    def _resolve_api_key(self) -> ProviderConfig:
        if self.api_key is not None:
            return self
        if not self.api_key_env:
            # Local runtimes (for example a loopback OpenAI-compatible server)
            # legitimately need no key; the adapter omits the header in that case.
            return self
        raw = _lookup_env(self.api_key_env)
        if not raw.strip():
            raise ValueError(
                f"environment variable {self.api_key_env!r} referenced by api_key_env is empty"
            )
        object.__setattr__(self, "api_key", SecretStr(raw.strip()))
        return self

    @property
    def api_key_value(self) -> str | None:
        """The resolved key, or ``None`` for keyless local providers."""
        return self.api_key.get_secret_value() if self.api_key is not None else None

    def safe_summary(self, *, effective_timeout: float | None = None) -> dict[str, Any]:
        """A log- and CLI-safe description. Never contains the credential.

        ``effective_timeout`` is the timeout the adapter will really use, which is
        this provider's own value when it sets one and the deployment default
        otherwise.
        """
        return {
            "name": self.name,
            "type": self.type,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "authenticated": self.api_key is not None,
            "api_key_source": "inline" if self.api_key_env is None else f"env:{self.api_key_env}",
            "timeout_seconds": effective_timeout
            if effective_timeout is not None
            else self.timeout_seconds,
            "timeout_source": "provider" if self.timeout_seconds else "AI_REQUEST_TIMEOUT_SECONDS",
        }


def parse_provider_configs(raw: str, *, default_provider: str) -> dict[str, ProviderConfig]:
    """Parse the ``AI_PROVIDERS`` JSON document into validated provider configs."""
    if not raw.strip():
        raise ConfigurationError(
            "AI_PROVIDERS is empty; set it to a JSON object describing at least one provider"
        )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"AI_PROVIDERS is not valid JSON: {exc}") from exc
    if not isinstance(document, dict) or not document:
        raise ConfigurationError("AI_PROVIDERS must be a non-empty JSON object")

    providers: dict[str, ProviderConfig] = {}
    for name, payload in document.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("AI_PROVIDERS keys must be non-empty provider names")
        if not isinstance(payload, dict):
            raise ConfigurationError(f"AI_PROVIDERS entry {name!r} must be a JSON object")
        try:
            providers[name] = ProviderConfig(name=name, **payload)
        except PydanticValidationError as exc:
            raise ConfigurationError(f"AI_PROVIDERS entry {name!r} is invalid: {exc}") from exc

    if default_provider not in providers:
        known = ", ".join(sorted(providers))
        raise ConfigurationError(
            f"DEFAULT_PROVIDER {default_provider!r} is not one of the configured providers ({known})"
        )
    return providers


@dataclass(frozen=True, slots=True)
class AIConfig:
    """Resolved AI settings shared by the registry and the services."""

    providers: dict[str, ProviderConfig]
    default_provider: str
    max_context_tokens: int
    request_timeout_seconds: float
    #: Ordered providers to try when the primary one fails.
    fallback_providers: tuple[str, ...] = ()
    max_retries: int = 3
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 8.0
    circuit_failure_threshold: int = 5
    circuit_reset_seconds: int = 60

    def effective_timeout(self, config: ProviderConfig) -> float:
        """Request timeout for a provider: its own value, or the deployment default."""
        return config.timeout_seconds or self.request_timeout_seconds

    def with_resolved_timeout(self, config: ProviderConfig) -> ProviderConfig:
        """A copy of the provider config with a concrete timeout."""
        if config.timeout_seconds is not None:
            return config
        return config.model_copy(update={"timeout_seconds": self.request_timeout_seconds})

    def provider(self, name: str | None) -> ProviderConfig:
        """Resolve a provider by name, falling back to the configured default."""
        resolved = name or self.default_provider
        try:
            return self.providers[resolved]
        except KeyError as exc:
            known = ", ".join(sorted(self.providers))
            raise ConfigurationError(
                f"provider {resolved!r} is not configured (available: {known})"
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.providers))
