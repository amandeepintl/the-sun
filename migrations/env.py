"""Alembic environment.

The database URL is resolved in this order:

1. ``config.attributes["database_url"]`` - set by the integration test fixtures
   so migrations run against the test database;
2. ``-x db_url=...`` on the command line;
3. ``Settings()`` - that is, the environment / ``.env`` file.

Nothing is read from ``alembic.ini``, so credentials stay out of the repository.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from the_sun.config import Settings
from the_sun.db.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would permanently mute
    # every logger created before migrations run (all of this project's module
    # loggers, in tests and in any process that migrates after importing).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    from_attributes = config.attributes.get("database_url")
    if isinstance(from_attributes, str) and from_attributes:
        return from_attributes
    overrides = context.get_x_argument(as_dictionary=True)
    if overrides.get("db_url"):
        return overrides["db_url"]
    return Settings().database.url


def run_migrations_offline() -> None:
    """Emit SQL without connecting."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    engine = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
