"""Unit of work against real PostgreSQL.

These tests commit and roll back for real: an owned unit of work ends its
transaction, while one that joins a caller's transaction only releases a
SAVEPOINT. Rows created here are explicitly removed again.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from the_sun.db import Database
from the_sun.repositories import UnitOfWork, build_unit_of_work_factory


async def test_committed_work_persists_until_it_is_deleted(
    database: Database, unique_id: int
) -> None:
    factory = build_unit_of_work_factory(database)

    async with factory() as uow:
        await uow.memories.upsert(user_id=unique_id, key="durability", value="written")
        assert uow.owns_session is True

    async with factory() as uow:
        stored = await uow.memories.get(user_id=unique_id, key="durability", guild_id=None)
        assert stored is not None
        assert stored.value == "written"

    async with factory() as uow:
        assert await uow.memories.delete_all_for_user(unique_id) == 1

    async with factory() as uow:
        assert await uow.memories.list_for_user(unique_id) == []


async def test_failed_work_is_rolled_back(database: Database, unique_id: int) -> None:
    factory = build_unit_of_work_factory(database)

    with pytest.raises(RuntimeError):
        async with factory() as uow:
            await uow.memories.upsert(user_id=unique_id, key="doomed", value="never committed")
            raise RuntimeError("simulated failure inside the transaction")

    async with factory() as uow:
        assert await uow.memories.list_for_user(unique_id) == []


async def test_joining_a_caller_transaction_only_releases_a_savepoint(
    uow_factory: Callable[[], UnitOfWork], unique_id: int
) -> None:
    async with uow_factory() as uow:
        assert uow.owns_session is False
        await uow.memories.upsert(user_id=unique_id, key="scoped", value="visible here")

    async with uow_factory() as uow:
        assert await uow.memories.get(user_id=unique_id, key="scoped", guild_id=None) is not None


async def test_repositories_are_cached_per_unit_of_work(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    async with uow_factory() as uow:
        assert uow.users is uow.users
        assert uow.memories is uow.memories
        assert uow.usage is uow.usage


async def test_using_a_unit_of_work_outside_its_context_fails_cleanly(database: Database) -> None:
    unit_of_work = UnitOfWork(database.session_factory)
    with pytest.raises(RuntimeError):
        _ = unit_of_work.session
