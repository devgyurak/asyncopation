"""fetch_all / execute / execute_many semantics (rowcount, lastrowid, RETURNING, atomicity)."""

from django.db import IntegrityError

from asyncopation.drivers.base import ExecResult
from tests.support.mysql_env import (
    MARIADB_ONLY,
    MYSQL_FAMILY,
    MYSQL_ONLY,
    insert_named,
    scratch_count,
    scratch_names,
    scratch_rows,
)
from tests.support.mysql_reference import compare_with_sync
from tests.support.mysql_scenarios import failing_batch, seed_four

pytestmark = MYSQL_FAMILY

INSERT = "INSERT INTO {} (name, qty) VALUES (%s, %s)"


async def test_execute_insert_reports_rowcount(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(INSERT.format(scratch), ("a", 1))
    # then
    assert result.rowcount == 1
    assert result.rows is None


@MYSQL_ONLY
async def test_execute_insert_reports_lastrowid_on_mysql(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a")
        # when
        result = await conn.execute(INSERT.format(scratch), ("b", 2))
    # then
    assert result.lastrowid == 2


@MARIADB_ONLY
async def test_execute_insert_returning_exposes_rows_without_lastrowid_on_mariadb(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(
            f"INSERT INTO {scratch} (name, qty) VALUES (%s, %s) RETURNING name, qty", ("a", 5)
        )
    # then
    assert result.rows == [("a", 5)]
    assert result.lastrowid is None


@MARIADB_ONLY
async def test_execute_many_with_returning_inserts_every_set_and_collects_no_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute_many(
            f"INSERT INTO {scratch} (name, qty) VALUES (%s, %s) RETURNING id",
            [("a", 1), ("b", 2)],
        )
    # then
    assert result.rows is None
    assert await scratch_names(scratch) == ["a", "b"]


async def test_execute_update_of_an_unchanged_row_counts_it_as_matched(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a", 5)
        # when
        result = await conn.execute(f"UPDATE {scratch} SET qty = %s WHERE name = %s", (5, "a"))
    # then
    assert result.rowcount == 1


async def test_execute_update_counts_matched_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a")
        await insert_named(conn, scratch, "b")
        # when
        result = await conn.execute(f"UPDATE {scratch} SET qty = %s", (3,))
    # then
    assert result.rowcount == 2


async def test_execute_update_without_match_has_zero_rowcount(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(f"UPDATE {scratch} SET qty = 1 WHERE name = %s", ("none",))
    # then
    assert result.rowcount == 0


async def test_execute_delete_counts_deleted_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a")
        # when
        result = await conn.execute(f"DELETE FROM {scratch}", None)
    # then
    assert result.rowcount == 1


async def test_execute_select_returns_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3", None)
    # then
    assert result.rows == [(1,), (2,), (3,)]


async def test_execute_outside_a_transaction_autocommits(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        await insert_named(conn, scratch, "a")
    # then
    assert await scratch_names(scratch) == ["a"]


async def test_fetch_all_reads_back_written_rows_in_order(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "b", 2)
        await insert_named(conn, scratch, "a", 1)
        # when
        rows = await scratch_rows(conn, scratch)
    # then
    assert rows == [("b", 2), ("a", 1)]


async def test_fetch_all_returns_the_same_rows_and_types_as_the_sync_cursor(readonly_db):
    # when
    result = await compare_with_sync("SELECT 1, 'a', 1.5, NULL, 2.5E0")
    # then
    assert result.async_rows == result.sync_rows


async def test_execute_many_inserts_every_parameter_set(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute_many(INSERT.format(scratch), [("a", 1), ("b", 2), ("c", 3)])
    # then
    assert result.rowcount == 3
    assert await scratch_names(scratch) == ["a", "b", "c"]


async def test_execute_many_with_empty_batch_inserts_nothing(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        await conn.execute_many(INSERT.format(scratch), [])
    # then
    assert await scratch_count(scratch) == 0


async def test_failing_multi_row_insert_batch_raises_integrity_error(driver, scratch):
    # when
    error, _ = await failing_batch(
        driver, scratch, INSERT.format(scratch), [("a", 1), ("b", 2), ("a", 3), ("d", 4), ("e", 5)]
    )
    # then
    assert type(error) is IntegrityError


async def test_failing_multi_row_insert_batch_leaves_no_rows_behind(driver, scratch):
    # when
    _, rows = await failing_batch(
        driver, scratch, INSERT.format(scratch), [("a", 1), ("b", 2), ("a", 3), ("d", 4), ("e", 5)]
    )
    # then
    assert rows == []


async def test_failing_update_batch_raises_integrity_error(driver, scratch):
    # given
    await seed_four(driver, scratch)
    # when
    error, _ = await failing_batch(
        driver,
        scratch,
        f"UPDATE {scratch} SET qty = %s WHERE name = %s",
        [(1, "a"), (2, "b"), (-5, "c"), (4, "d")],
    )
    # then
    assert type(error) is IntegrityError


async def test_failing_update_batch_is_not_atomic(driver, scratch):
    # given
    await seed_four(driver, scratch)
    # when
    _, rows = await failing_batch(
        driver,
        scratch,
        f"UPDATE {scratch} SET qty = %s WHERE name = %s",
        [(1, "a"), (2, "b"), (-5, "c"), (4, "d")],
    )
    # then
    assert rows == [("a", 1), ("b", 2), ("c", 0), ("d", 0)]


async def test_driver_declares_non_atomic_execute_many(driver):
    # when
    capabilities = driver.capabilities
    # then
    assert capabilities.atomic_execute_many is False


async def test_execute_many_update_reports_the_batch_rowcount(driver, scratch):
    # given
    await seed_four(driver, scratch)
    async with driver.connection() as conn:
        # when
        result = await conn.execute_many(
            f"UPDATE {scratch} SET qty = %s WHERE name = %s", [(1, "a"), (2, "b")]
        )
    # then
    assert isinstance(result, ExecResult)
    assert result.rowcount == 2
