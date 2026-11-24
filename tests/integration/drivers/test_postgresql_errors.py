"""Driver errors are django.db exceptions chained to the psycopg exception.

Pool expectations after a discarded (killed / FATAL) connection: psycopg_pool replaces it
unconditionally, even with `min_size=0` - `AsyncConnectionPool._return_connection` logs
"discarding closed connection" and calls `self.run_task(AddConnection(self))` without a
`min_size` check - so the pool settles at one idle connection, not zero.
"""

import psycopg
import pytest
from django.db import DataError, IntegrityError, InternalError, OperationalError, ProgrammingError

from asyncopation.drivers.base import PoolStats
from tests.support.drivers import (
    POSTGRES_ONLY,
    acquire_while_saturated,
    consume_failing_stream,
    error_name,
    fail_in_transaction_then_rollback,
    failed_commit_aftermath,
    idle_timeout_then_query,
    is_pool_timeout,
    kill_backend_then_query,
    savepoint_after_integrity_error,
    scratch_names,
    stream_error_after_kill,
    sync_idle_timeout_error_name,
)

pytestmark = POSTGRES_ONLY


@pytest.mark.parametrize(
    ("sql", "django_exc", "cause"),
    [
        pytest.param(
            "INSERT INTO {t} (name) SELECT 'dup' FROM generate_series(1, 2)",
            IntegrityError,
            psycopg.errors.UniqueViolation,
            id="unique",
        ),
        pytest.param(
            "INSERT INTO catalog_authorprofile (author_id, bio, website) VALUES (-1, 'b', 'w')",
            IntegrityError,
            psycopg.errors.ForeignKeyViolation,
            id="foreign-key",
        ),
        pytest.param(
            "INSERT INTO {t} (name) VALUES (NULL)",
            IntegrityError,
            psycopg.errors.NotNullViolation,
            id="not-null",
        ),
        pytest.param(
            "INSERT INTO {t} (name, qty) VALUES ('c', -1)",
            IntegrityError,
            psycopg.errors.CheckViolation,
            id="check",
        ),
        pytest.param("SELECT 1/0", DataError, psycopg.errors.DivisionByZero, id="data"),
        pytest.param("SELEC 1", ProgrammingError, psycopg.errors.SyntaxError, id="syntax"),
        pytest.param(
            "SELECT * FROM {t}_missing", ProgrammingError, psycopg.errors.UndefinedTable, id="table"
        ),
    ],
)
async def test_execute_raises_mapped_exception_with_driver_cause(
    driver, scratch, sql, django_exc, cause
):
    # given
    async with driver.connection() as conn:
        # when / then
        with pytest.raises(django_exc) as caught:
            await conn.execute(sql.format(t=scratch), None)
    assert type(caught.value) is django_exc
    assert isinstance(caught.value.__cause__, cause)


async def test_fetch_all_raises_mapped_exception(driver):
    # given
    async with driver.connection() as conn:
        # when / then
        with pytest.raises(DataError):
            await conn.fetch_all("SELECT 1/0", None)


async def test_connection_is_reusable_after_integrity_error(driver, scratch):
    # given
    async with driver.connection() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(f"INSERT INTO {scratch} (name) VALUES (NULL)", None)
        # when
        rows = await conn.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


async def test_integrity_error_keeps_connection_in_the_pool(single_driver, scratch):
    # given
    async with single_driver.connection() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(f"INSERT INTO {scratch} (name) VALUES (NULL)", None)
    # when
    stats = single_driver.pool_stats()
    # then
    assert stats == PoolStats(in_use=0, idle=1, waiting=0)


async def test_killed_backend_raises_operational_error_with_driver_cause(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver)
    # then
    assert type(outcome.error) is OperationalError
    assert isinstance(outcome.error.__cause__, psycopg.OperationalError)


async def test_killed_connection_is_not_returned_to_the_pool(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_borrow_after_killed_connection_uses_a_fresh_backend(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver)
    # then
    assert outcome.fresh_rows == [(1,)]
    assert outcome.fresh_pid != outcome.killed_pid


async def test_killed_backend_error_is_a_disconnect(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver)
    # then
    assert elastic_driver.is_disconnect(outcome.error) is True


async def test_backend_killed_during_transaction_raises_operational_error(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver, transactional=True)
    # then
    assert type(outcome.error) is OperationalError


async def test_pool_replaces_connection_killed_during_transaction(elastic_driver):
    # when
    outcome = await kill_backend_then_query(elastic_driver, transactional=True)
    # then
    assert outcome.fresh_rows == [(1,)]
    assert outcome.fresh_pid != outcome.killed_pid
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_fatal_idle_timeout_raises_internal_error_like_the_sync_backend(
    readonly_db, idle_timeout_driver
):
    # when
    outcome = await idle_timeout_then_query(idle_timeout_driver)
    sync_name = await sync_idle_timeout_error_name()
    # then
    assert error_name(outcome.error) == sync_name
    assert type(outcome.error) is InternalError


async def test_fatal_idle_timeout_error_is_a_disconnect(idle_timeout_driver):
    # when
    outcome = await idle_timeout_then_query(idle_timeout_driver)
    # then
    assert idle_timeout_driver.is_disconnect(outcome.error) is True


async def test_fatal_idle_timeout_discards_the_connection(idle_timeout_driver):
    # when
    outcome = await idle_timeout_then_query(idle_timeout_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.fresh_pid != outcome.killed_pid


async def test_savepoint_rollback_after_integrity_error_keeps_transaction_usable(driver, scratch):
    # given
    async with driver.connection() as conn:
        # when
        error = await savepoint_after_integrity_error(conn, scratch)
    # then
    assert isinstance(error, IntegrityError)
    assert await scratch_names(scratch) == ["a", "b"]


async def test_failed_statement_then_rollback_leaves_a_reusable_connection(single_driver):
    # given
    async with single_driver.connection() as conn:
        # when
        outcome = await fail_in_transaction_then_rollback(conn)
    # then
    assert isinstance(outcome.error, DataError)
    assert outcome.rows_after == [(1,)]
    assert outcome.still_in_transaction is False


STREAM_ROWS_SEEN = [
    pytest.param(False, 0, id="autocommit-with-hold-fails-at-declare"),
    pytest.param(True, 4, id="transaction-fails-at-the-third-fetch"),
]


@pytest.mark.parametrize(("transactional", "rows_seen"), STREAM_ROWS_SEEN)
async def test_stream_error_midway_maps_to_data_error_with_driver_cause(
    driver, transactional, rows_seen
):
    # given
    async with driver.connection() as conn:
        # when
        outcome = await consume_failing_stream(conn, transactional=transactional)
    # then
    assert type(outcome.error) is DataError
    assert isinstance(outcome.error.__cause__, psycopg.errors.DivisionByZero)
    assert outcome.rows_seen == rows_seen


@pytest.mark.parametrize("transactional", [False, True], ids=["autocommit", "transaction"])
async def test_stream_error_midway_leaves_no_cursor_behind(driver, transactional):
    # given
    async with driver.connection() as conn:
        # when
        outcome = await consume_failing_stream(conn, transactional=transactional)
    # then
    assert outcome.cursors_after == []


async def test_acquire_timeout_raises_operational_error_caused_by_pool_timeout(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert type(outcome.error) is OperationalError
    assert is_pool_timeout(outcome.error)


async def test_acquire_timeout_does_not_leak_a_connection(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_failed_commit_raises_integrity_error(single_driver, deferred):
    # when
    outcome = await failed_commit_aftermath(single_driver, deferred)
    # then
    assert type(outcome.error) is IntegrityError


async def test_failed_commit_leaves_the_connection_in_autocommit(single_driver, deferred):
    # when
    outcome = await failed_commit_aftermath(single_driver, deferred)
    # then
    assert outcome.still_in_transaction is False


async def test_statement_after_a_failed_commit_autocommits(single_driver, deferred):
    # when
    outcome = await failed_commit_aftermath(single_driver, deferred)
    # then
    assert outcome.visible_ids == [2]


async def test_stream_without_server_side_cursors_on_a_dead_connection_raises_django_error(
    no_cursor_driver,
):
    # when
    error = await stream_error_after_kill(no_cursor_driver)
    # then
    assert type(error) is OperationalError


async def test_stream_with_server_side_cursors_on_a_dead_connection_raises_django_error(driver):
    # when
    error = await stream_error_after_kill(driver)
    # then
    assert type(error) is OperationalError
