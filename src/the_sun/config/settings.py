"""Application settings.

Every dynamic value the process needs comes from the environment (or from a
gitignored ``.env`` file). Nothing in this module - or anywhere else under
``src/`` - hardcodes a Discord ID, a server ID, a credential, a model name or a
user-visible AI response.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from the_sun.config.prompts import DEFAULT_SYSTEM_PROMPT
from the_sun.config.providers import AIConfig, ProviderConfig, parse_provider_configs
from the_sun.errors import ConfigurationError

__all__ = ["CacheConfig", "DatabaseConfig", "Settings", "get_settings", "reset_settings_cache"]


def _mask_url_password(raw: str) -> str:
    """Return a URL safe for logs and CLI output, with any password removed."""
    if "@" not in raw:
        return raw
    scheme_sep = raw.find("://")
    if scheme_sep == -1:
        return raw
    head, _, tail = raw.rpartition("@")
    scheme_and_creds = head
    creds_sep = scheme_and_creds.find("://")
    prefix = scheme_and_creds[: creds_sep + 3]
    creds = scheme_and_creds[creds_sep + 3 :]
    user, _, _ = creds.partition(":")
    return f"{prefix}{user}:***@{tail}"


def _normalise_postgres_url(raw: str, *, source: str) -> tuple[str, str]:
    """Validate a PostgreSQL URL and force the asyncpg driver.

    Returns the SQLAlchemy async URL plus a password-free rendering of it.
    """
    candidate = raw.strip()
    if not candidate:
        raise ConfigurationError(f"{source} is empty")
    try:
        url = make_url(candidate)
    except Exception as exc:
        raise ConfigurationError(f"{source} is not a valid database URL: {exc}") from exc
    if not url.drivername.startswith("postgres"):
        raise ConfigurationError(
            f"{source} must be a PostgreSQL URL (got scheme {url.drivername!r}); "
            "this project targets PostgreSQL exclusively"
        )
    async_url = url.set(drivername="postgresql+asyncpg")
    return async_url.render_as_string(hide_password=False), async_url.render_as_string(
        hide_password=True
    )


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Resolved database connection and pool settings."""

    url: str
    safe_url: str
    pool_size: int
    max_overflow: int
    pool_recycle_seconds: int
    echo: bool


@dataclass(frozen=True, slots=True)
class CacheConfig:
    """Resolved Redis connection settings."""

    url: str
    safe_url: str
    key_prefix: str
    max_connections: int


class Settings(BaseSettings):
    """Environment-backed settings.

    Field aliases match the documented environment variable names exactly so the
    ``.env`` file stays a flat, readable list.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")

    discord_token: SecretStr = Field(alias="DISCORD_TOKEN")
    #: Set this to a guild id during development for instant command syncing.
    dev_guild_id: int | None = Field(default=None, alias="DEV_GUILD_ID")
    #: Whether the privileged Message Content intent is enabled in the portal.
    #: Commands work without it; summarising other members' messages needs it.
    discord_message_content_intent: bool = Field(
        default=True, alias="DISCORD_MESSAGE_CONTENT_INTENT"
    )

    database_url: str = Field(alias="DATABASE_URL")
    test_database_url: str | None = Field(default=None, alias="TEST_DATABASE_URL")
    db_pool_size: int = Field(default=5, ge=1, le=100, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, ge=0, le=100, alias="DB_MAX_OVERFLOW")
    db_pool_recycle_seconds: int = Field(default=1800, ge=60, alias="DB_POOL_RECYCLE_SECONDS")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    redis_url: str = Field(alias="REDIS_URL")
    redis_key_prefix: str = Field(default="thesun", min_length=1, alias="REDIS_KEY_PREFIX")
    redis_max_connections: int = Field(default=50, ge=1, alias="REDIS_MAX_CONNECTIONS")

    ai_providers: str = Field(alias="AI_PROVIDERS")
    default_provider: str = Field(alias="DEFAULT_PROVIDER")
    ai_max_context_tokens: int = Field(default=8192, ge=256, alias="AI_MAX_CONTEXT_TOKENS")
    ai_request_timeout_seconds: float = Field(
        default=60.0, gt=0, le=600, alias="AI_REQUEST_TIMEOUT_SECONDS"
    )
    ai_fallback_providers: str = Field(default="", alias="AI_FALLBACK_PROVIDERS")
    ai_max_retries: int = Field(default=3, ge=1, le=10, alias="AI_MAX_RETRIES")
    ai_retry_base_seconds: float = Field(default=0.5, gt=0, le=60, alias="AI_RETRY_BASE_SECONDS")
    ai_retry_max_seconds: float = Field(default=8.0, gt=0, le=300, alias="AI_RETRY_MAX_SECONDS")
    ai_circuit_failure_threshold: int = Field(
        default=5, ge=1, le=100, alias="AI_CIRCUIT_FAILURE_THRESHOLD"
    )
    ai_circuit_reset_seconds: int = Field(
        default=60, ge=1, le=3600, alias="AI_CIRCUIT_RESET_SECONDS"
    )
    #: Instructions given to the model. Replaceable per deployment and, inside a
    #: guild, with ``/settings prompt``.
    ai_system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, alias="AI_SYSTEM_PROMPT")
    ai_max_output_tokens: int = Field(default=1024, ge=16, le=8192, alias="AI_MAX_OUTPUT_TOKENS")
    #: Left unset, the provider's own default sampling is used.
    ai_temperature: float | None = Field(default=None, ge=0.0, le=2.0, alias="AI_TEMPERATURE")

    #: How much real channel history a summary may read and how much of each
    #: message is handed to the model.
    summary_max_messages: int = Field(default=100, ge=1, le=500, alias="SUMMARY_MAX_MESSAGES")
    summary_max_chars_per_message: int = Field(
        default=1200, ge=100, le=8000, alias="SUMMARY_MAX_CHARS_PER_MESSAGE"
    )

    #: Per-minute windows applied when a guild has not configured its own limits.
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    default_rate_limit_per_user_per_minute: int = Field(
        default=6, ge=1, le=600, alias="DEFAULT_RATE_LIMIT_PER_USER_PER_MINUTE"
    )
    default_rate_limit_per_guild_per_minute: int = Field(
        default=30, ge=1, le=6000, alias="DEFAULT_RATE_LIMIT_PER_GUILD_PER_MINUTE"
    )

    #: Caps that keep the explicitly saved memory store small and predictable.
    memory_max_entries: int = Field(default=100, ge=1, le=1000, alias="MEMORY_MAX_ENTRIES")
    memory_max_value_chars: int = Field(
        default=1000, ge=10, le=4000, alias="MEMORY_MAX_VALUE_CHARS"
    )

    #: How many stored turns ``/history`` shows per page.
    history_page_size: int = Field(default=10, ge=1, le=25, alias="HISTORY_PAGE_SIZE")

    #: Retention. ``DEFAULT_HISTORY_RETENTION_DAYS`` unset means stored history is
    #: kept until it is deleted explicitly (``/privacy forget me``); a guild can
    #: set its own window with ``/settings retention``.
    default_history_retention_days: int | None = Field(
        default=None, ge=1, le=3650, alias="DEFAULT_HISTORY_RETENTION_DAYS"
    )
    usage_retention_days: int = Field(default=90, ge=0, le=3650, alias="USAGE_RETENTION_DAYS")
    #: How often the bot runs the retention purge. 0 disables the background task.
    retention_interval_hours: int = Field(
        default=24, ge=0, le=168, alias="RETENTION_INTERVAL_HOURS"
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_json: bool = Field(default=True, alias="LOG_JSON")

    #: History window for direct messages, which have no guild settings row.
    default_history_length: int = Field(default=20, ge=1, le=200, alias="DEFAULT_HISTORY_LENGTH")

    metrics_enabled: bool = Field(default=False, alias="METRICS_ENABLED")
    health_host: str = Field(default="127.0.0.1", alias="HEALTH_HOST")
    health_port: int = Field(default=8080, ge=1, le=65535, alias="HEALTH_PORT")

    # ----------------------------------------------------------------- #
    # Grouped views. Parsing and validation errors surface here with the
    # variable name that caused them.
    # ----------------------------------------------------------------- #
    @cached_property
    def ai(self) -> AIConfig:
        providers: dict[str, ProviderConfig] = parse_provider_configs(
            self.ai_providers, default_provider=self.default_provider
        )
        fallbacks = tuple(
            name.strip() for name in self.ai_fallback_providers.split(",") if name.strip()
        )
        unknown = [name for name in fallbacks if name not in providers]
        if unknown:
            known = ", ".join(sorted(providers))
            raise ConfigurationError(
                f"AI_FALLBACK_PROVIDERS lists unknown provider(s) {', '.join(unknown)} "
                f"(configured: {known})"
            )
        return AIConfig(
            providers=providers,
            default_provider=self.default_provider,
            max_context_tokens=self.ai_max_context_tokens,
            request_timeout_seconds=self.ai_request_timeout_seconds,
            fallback_providers=fallbacks,
            max_retries=self.ai_max_retries,
            retry_base_seconds=self.ai_retry_base_seconds,
            retry_max_seconds=self.ai_retry_max_seconds,
            circuit_failure_threshold=self.ai_circuit_failure_threshold,
            circuit_reset_seconds=self.ai_circuit_reset_seconds,
        )

    @cached_property
    def database(self) -> DatabaseConfig:
        url, safe_url = _normalise_postgres_url(self.database_url, source="DATABASE_URL")
        return DatabaseConfig(
            url=url,
            safe_url=safe_url,
            pool_size=self.db_pool_size,
            max_overflow=self.db_max_overflow,
            pool_recycle_seconds=self.db_pool_recycle_seconds,
            echo=self.db_echo,
        )

    @cached_property
    def cache(self) -> CacheConfig:
        url = self.redis_url.strip()
        if not url:
            raise ConfigurationError("REDIS_URL is empty")
        if not url.startswith(("redis://", "rediss://", "unix://")):
            raise ConfigurationError("REDIS_URL must use the redis://, rediss:// or unix:// scheme")
        return CacheConfig(
            url=url,
            safe_url=_mask_url_password(url),
            key_prefix=self.redis_key_prefix.strip(),
            max_connections=self.redis_max_connections,
        )

    def test_database(self) -> DatabaseConfig:
        """Database used by integration tests: ``TEST_DATABASE_URL`` when set."""
        raw = (self.test_database_url or "").strip() or self.database_url
        url, safe_url = _normalise_postgres_url(raw, source="TEST_DATABASE_URL/DATABASE_URL")
        return DatabaseConfig(
            url=url,
            safe_url=safe_url,
            pool_size=self.db_pool_size,
            max_overflow=self.db_max_overflow,
            pool_recycle_seconds=self.db_pool_recycle_seconds,
            echo=self.db_echo,
        )

    @property
    def discord_token_value(self) -> str:
        return self.discord_token.get_secret_value()

    @property
    def discord_token_description(self) -> str:
        """A length-only description of the token, safe for logs and CLI output."""
        return f"<set, {len(self.discord_token_value)} characters>"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide settings instance, building it on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Drop the cached instance. Used by tests and by the CLI reload path."""
    global _settings
    _settings = None
