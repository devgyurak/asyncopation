"""begin / commit / rollback and savepoints on a pooled connection."""

import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import (
    MYSQL_FAMILY,
    insert_named,
    scratch_count,
    scratch_names,
    settled_stats,
)
from tests.support.mysql_scenarios import rows_of

pytestmark = MYSQL_FAMILY


async def test_committed_rows_become_visible(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        # when
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_uncommitted_rows_are_invisible_to_other_connections(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        # when
        await insert_named(conn, scratch, "a")
        visible = await scratch_count(scratch)
        await conn.rollback()
    # then
    assert visible == 0


async def test_rolled_back_rows_disappear(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        # when
        await conn.rollback()
    # then
    assert await scratch_count(scratch) == 0


async def test_commit_returns_the_connection_to_autocommit(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await conn.commit()
        # when
        await insert_named(conn, scratch, "a")
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_rollback_returns_the_connection_to_autocommit(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await conn.rollback()
        # when
        await insert_named(conn, scratch, "a")
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_a_connection_released_inside_a_transaction_is_rolled_back(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
    # when
    autocommit = (await rows_of(single_driver, "SELECT @@autocommit"))[0][0]
    # then
    assert await scratch_count(scratch) == 0
    assert autocommit == 1


async def test_a_connection_released_inside_a_transaction_returns_to_the_pool(single_driver):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
    # when
    stats = await settled_stats(single_driver, min_idle=1)
    # then
    assert stats == PoolStats(in_use=0, idle=1, waiting=0)


async def test_rollback_to_savepoint_undoes_only_later_changes(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await insert_named(conn, scratch, "a")
        await conn.savepoint("sp1")
        await insert_named(conn, scratch, "b")
        # when
        await conn.rollback_to_savepoint("sp1")
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_release_savepoint_keeps_the_changes(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await conn.savepoint("sp1")
        await insert_named(conn, scratch, "a")
        # when
        await conn.release_savepoint("sp1")
        await conn.commit()
    # then
    assert await scratch_names(scratch) == ["a"]


@pytest.mark.parametrize("name", ["select", "my savepoint", "sp-1", "spé", "sp;1"])
async def test_savepoint_names_are_quoted(single_driver, scratch, name):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await conn.savepoint(name)
        await insert_named(conn, scratch, "a")
        # when
        await conn.rollback_to_savepoint(name)
        await conn.release_savepoint(name)
        await conn.commit()
    # then
    assert await scratch_count(scratch) == 0


async def test_nested_savepoints_roll_back_to_the_named_one(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        await conn.begin()
        await conn.savepoint("outer")
        await insert_named(conn, scratch, "a")
        await conn.savepoint("inner")
        await insert_named(conn, scratch, "b")
        # when
        await conn.rollback_to_savepoint("outer")
        await conn.commit()
    # then
    assert await scratch_count(scratch) == 0
