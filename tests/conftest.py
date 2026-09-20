"""Shared test fixtures.

Unit tests build settings from explicit values and never touch the network.
Integration tests use the values from the real ``.env`` and fail loudly if the
infrastructure is missing, because substituting a fake database or cache would
mean the suite stops proving anything about production behaviour.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from the_sun.cache import CacheBackend, RedisCacheBackend, create_cache_backend
from the_sun.config import CacheConfig, DatabaseConfig, Settings
from the_sun.db import Database, create_database
from the_sun.repositories import UnitOfWork
from the_sun.services import ServiceContext

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

#: Test directory -> marker applied to everything inside it, so
#: ``pytest -m unit`` and ``pytest -m integration`` select whole layers.
DIRECTORY_MARKERS = {
    "unit": pytest.mark.unit,
    "integration": pytest.mark.integration,
    "live": pytest.mark.live,
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach the layer marker implied by each test file's directory."""
    for item in items:
        parts = set(Path(str(item.fspath)).parts)
        for directory, marker in DIRECTORY_MARKERS.items():
            if directory in parts:
                item.add_marker(marker)
                break


#: Non-resolvable host: an accidental network call in a unit test fails fast
#: instead of reaching a real service.
UNIT_PROVIDER_BASE_URL = "https://provider.invalid/v1"
UNIT_MODEL_ID = "unit-test-model"

INTEGRATION_HINT = (
    f"integration tests need real infrastructure configured in {ENV_FILE}. "
    "Create it from .env.example and fill in DISCORD_TOKEN, DATABASE_URL, REDIS_URL, "
    "AI_PROVIDERS and DEFAULT_PROVIDER. Postgres and Redis may also be supplied through "
    "TEST_DATABASE_URL (a scratch database) instead of faking them."
)


def unit_provider_document(**overrides: object) -> str:
    """Build the AI_PROVIDERS JSON used by unit tests."""
    provider: dict[str, object] = {
        "type": "openai_compatible",
        "base_url": UNIT_PROVIDER_BASE_URL,
        "api_key": "unit-test-api-key",
        "default_model": UNIT_MODEL_ID,
    }
    provider.update(overrides)
    return json.dumps({"primary": provider})


@pytest.fixture
def unit_settings() -> Settings:
    """Settings built from explicit test values, ignoring any .env file."""
    return Settings(
        _env_file=None,
        DISCORD_TOKEN="unit-test-discord-token",
        DATABASE_URL="postgresql://unit:unit@localhost:5432/unit",
        REDIS_URL="redis://localhost:6379/0",
        AI_PROVIDERS=unit_provider_document(),
        DEFAULT_PROVIDER="primary",
    )


# --------------------------------------------------------------------------- #
# Integration infrastructure
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Real settings read from .env; fails the suite when they are unusable."""
    try:
        settings = Settings(_env_file=ENV_FILE)
    except PydanticValidationError as exc:
        missing = sorted({str(error["loc"][0]) for error in exc.errors()})
        pytest.fail(
            f"{INTEGRATION_HINT}\nMissing or invalid: {', '.join(missing)}",
            pytrace=False,
        )
    if not ENV_FILE.exists():
        pytest.fail(f"{ENV_FILE} does not exist. {INTEGRATION_HINT}", pytrace=False)
    return settings


@pytest.fixture(scope="session")
def test_database_config(integration_settings: Settings) -> DatabaseConfig:
    """Database used by integration tests (TEST_DATABASE_URL when configured)."""
    return integration_settings.test_database()


@pytest.fixture(scope="session")
def migrated_database(test_database_config: DatabaseConfig) -> DatabaseConfig:
    """Apply every migration to the test database once per session."""
    alembic_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    alembic_config.attributes["database_url"] = test_database_config.url
    try:
        command.upgrade(alembic_config, "head")
    except Exception as exc:
        pytest.fail(
            f"could not migrate {test_database_config.safe_url}: {exc}\n{INTEGRATION_HINT}",
            pytrace=False,
        )
    return test_database_config


@pytest.fixture
async def database(migrated_database: DatabaseConfig) -> AsyncIterator[Database]:
    """A real engine bound to the test database, disposed after the test."""
    instance = create_database(migrated_database)
    try:
        yield instance
    finally:
        await instance.dispose()


@pytest.fixture
async def db_session(database: Database) -> AsyncIterator[AsyncSession]:
    """A real session wrapped in a transaction that is always rolled back."""
    async with database.session() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
def uow_factory(database: Database, db_session: AsyncSession) -> Callable[[], UnitOfWork]:
    """Factory handing out units of work that join the test transaction."""

    def factory() -> UnitOfWork:
        return UnitOfWork(database.session_factory, session=db_session)

    return factory


@pytest.fixture(scope="session")
def test_cache_config(integration_settings: Settings) -> CacheConfig:
    """Redis configuration using a test-only key prefix for isolation."""
    return replace(
        integration_settings.cache,
        key_prefix=f"{integration_settings.cache.key_prefix}-test",
    )


@pytest.fixture
async def cache(test_cache_config: CacheConfig) -> AsyncIterator[RedisCacheBackend]:
    """A real Redis connection whose keys are removed afterwards."""
    backend = create_cache_backend(test_cache_config)
    try:
        yield backend
    finally:
        await backend.delete_by_pattern(f"{test_cache_config.key_prefix}:*")
        await backend.close()


@pytest.fixture
async def service_context(
    integration_settings: Settings,
    database: Database,
    cache: CacheBackend,
    uow_factory: Callable[[], UnitOfWork],
) -> AsyncIterator[ServiceContext]:
    """A service context over real Postgres, real Redis and real provider config.

    It borrows the test's unit-of-work factory, so services read their own writes
    and everything is rolled back when the test finishes.
    """
    context = ServiceContext(
        integration_settings, database=database, cache=cache, uow_factory=uow_factory
    )
    try:
        yield context
    finally:
        await context.providers.aclose()


#: Test rows use plausible snowflake-shaped identifiers that cannot collide with
#: real Discord identifiers, and every test rolls its transaction back anyway.
_SNOWFLAKE_COUNTER = itertools.count(start=1)
_SNOWFLAKE_BASE = 9_000_000_000_000_000


@pytest.fixture
def unique_id() -> int:
    """A fresh 64-bit identifier for each use."""
    return _SNOWFLAKE_BASE + next(_SNOWFLAKE_COUNTER)
