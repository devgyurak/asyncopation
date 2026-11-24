"""fetch_all / execute / execute_many semantics (rowcount, RETURNING, atomicity)."""

import psycopg
from django.db import IntegrityError

from asyncopation.drivers.base import ExecResult
from tests.support.drivers import (
    POSTGRES_ONLY,
    failing_batch_error,
    insert_named,
    scratch_count,
    scratch_names,
    scratch_rows,
)

pytestmark = POSTGRES_ONLY


async def test_execute_insert_reports_rowcount_without_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(f"INSERT INTO {scratch} (name) VALUES (%s)", ("a",))
    # then
    assert result == ExecResult(rowcount=1)


async def test_execute_insert_returning_exposes_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(
            f"INSERT INTO {scratch} (name, qty) VALUES (%s, %s) RETURNING name, qty", ("a", 5)
        )
    # then
    assert result == ExecResult(rowcount=1, rows=[("a", 5)])


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
    assert result == ExecResult(rowcount=0)


async def test_execute_delete_counts_deleted_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a")
        # when
        result = await conn.execute(f"DELETE FROM {scratch}", None)
    # then
    assert result.rowcount == 1


async def test_execute_select_returns_rows_and_rowcount(driver):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute("SELECT g FROM generate_series(1, 3) g", None)
    # then
    assert result == ExecResult(rowcount=3, rows=[(1,), (2,), (3,)])


async def test_execute_outside_transaction_autocommits(driver, scratch):
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


async def test_execute_many_inserts_every_parameter_set(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute_many(
            f"INSERT INTO {scratch} (name, qty) VALUES (%s, %s)", [("a", 1), ("b", 2), ("c", 3)]
        )
    # then
    assert result.rowcount == 3
    assert await scratch_names(scratch) == ["a", "b", "c"]


async def test_execute_many_with_empty_batch_inserts_nothing(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        await conn.execute_many(f"INSERT INTO {scratch} (name, qty) VALUES (%s, %s)", [])
    # then
    assert await scratch_count(scratch) == 0


async def test_failing_execute_many_batch_raises_integrity_error(driver, scratch):
    # when
    error, _ = await failing_batch_error(driver, scratch)
    # then
    assert isinstance(error, IntegrityError)


async def test_failing_execute_many_batch_leaves_no_rows_behind(driver, scratch):
    # when
    _, rows_left = await failing_batch_error(driver, scratch)
    # then
    assert rows_left == 0


async def test_driver_declares_atomic_execute_many_when_pipelines_are_supported(driver):
    # when
    capabilities = driver.capabilities
    # then
    assert capabilities.atomic_execute_many is psycopg.AsyncPipeline.is_supported()


async def test_update_returning_without_match_yields_empty_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(f"UPDATE {scratch} SET qty = 1 WHERE false RETURNING id", None)
    # then
    assert result == ExecResult(rowcount=0, rows=[])


async def test_delete_returning_without_match_yields_empty_rows(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        result = await conn.execute(f"DELETE FROM {scratch} WHERE false RETURNING id", None)
    # then
    assert result == ExecResult(rowcount=0, rows=[])


async def test_execute_many_update_reports_the_batch_rowcount(driver, scratch):
    # given
    async with driver.connection() as conn:
        await insert_named(conn, scratch, "a")
        await insert_named(conn, scratch, "b")
        await insert_named(conn, scratch, "c")
        # when
        result = await conn.execute_many(
            f"UPDATE {scratch} SET qty = %s WHERE name = %s", [(1, "a"), (2, "b")]
        )
        rows = await scratch_rows(conn, scratch)
    # then
    assert result.rowcount == 2
    assert rows == [("a", 1), ("b", 2), ("c", 0)]
