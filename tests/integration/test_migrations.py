"""Migrations against a real PostgreSQL database.

Schema facts are read back through the production introspection repository rather
than through ad-hoc SQL, so this file also proves that repository works.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from the_sun.config import DatabaseConfig
from the_sun.db import Database
from the_sun.repositories.database_info import SqlAlchemyDatabaseInfoRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_REVISION = "0001_initial_schema"
EXPECTED_TABLES = [
    "alembic_version",
    "conversations",
    "guild_settings",
    "memories",
    "messages",
    "usage_events",
    "users",
]


def _alembic_config(database: DatabaseConfig) -> AlembicConfig:
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.attributes["database_url"] = database.url
    return config


async def test_every_designed_table_exists(database: Database) -> None:
    async with database.session() as session:
        repository = SqlAlchemyDatabaseInfoRepository(session)
        tables = await repository.table_names()
    assert tables == EXPECTED_TABLES


async def test_migration_revision_matches_the_head_revision(database: Database) -> None:
    async with database.session() as session:
        repository = SqlAlchemyDatabaseInfoRepository(session)
        assert await repository.applied_migration_revision() == EXPECTED_REVISION
        info = await repository.describe()
    assert info.server_version
    assert info.latency_ms > 0


def test_metadata_matches_the_database(migrated_database: DatabaseConfig) -> None:
    """``alembic check`` proves the models and the applied schema agree."""
    config = _alembic_config(migrated_database)
    command.check(config)


def test_downgrade_then_upgrade_returns_to_the_same_schema(
    migrated_database: DatabaseConfig,
) -> None:
    """The migration is reversible, which is what makes rollouts safe."""
    config = _alembic_config(migrated_database)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
