import pytest

from asyncopation.drivers.base import Capabilities, ExecResult
from asyncopation.testing import NoQueuedResult, RecordingDriver, Statement
from tests.support.drivers import (
    as_connection,
    as_driver,
    perform_operation,
    queue_error_then_rows,
    run_on_two_connections,
    statement_kinds,
    stream_chunks,
)

ALL_OPERATIONS = [
    "fetch_all",
    "execute",
    "execute_many",
    "stream",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release_savepoint",
    "rollback_to_savepoint",
]
RESULT_OPERATIONS = ["fetch_all", "execute", "execute_many", "stream"]


def test_recording_driver_satisfies_the_driver_protocol():
    # given
    driver = RecordingDriver()
    # when
    accepted = as_driver(driver)
    # then
    assert accepted is driver


async def test_recording_connection_satisfies_the_connection_protocol():
    # given
    driver = RecordingDriver()
    # when
    async with driver.connection() as conn:
        accepted = as_connection(conn)
    # then
    assert accepted is conn


async def test_default_capabilities_describe_a_modern_server():
    # given
    driver = RecordingDriver()
    # when
    await driver.open()
    # then
    assert driver.capabilities == Capabilities(
        server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True
    )


async def test_custom_capabilities_are_reported():
    # given
    custom = Capabilities(
        server_version=(11, 8), server_side_cursors=False, atomic_execute_many=False
    )
    # when
    driver = RecordingDriver(capabilities=custom)
    # then
    assert driver.capabilities == custom


async def test_open_and_close_flip_lifecycle_flags():
    # given
    driver = RecordingDriver()
    initial = (driver.opened, driver.closed)
    # when
    await driver.open()
    opened = (driver.opened, driver.closed)
    await driver.close()
    # then
    assert initial == (False, False)
    assert opened == (True, False)
    assert driver.closed is True


async def test_fetch_all_pops_queued_rows_in_fifo_order():
    # given
    driver = RecordingDriver()
    driver.queue([(1,)], [(2,), (3,)])
    # when
    async with driver.connection() as conn:
        first = await conn.fetch_all("SELECT a", None)
        second = await conn.fetch_all("SELECT b", None)
    # then
    assert (first, second) == ([(1,)], [(2,), (3,)])


async def test_fetch_all_uses_rows_of_a_queued_exec_result():
    # given
    driver = RecordingDriver()
    driver.queue(ExecResult(rowcount=1, rows=[(7,)]), ExecResult(rowcount=0))
    # when
    async with driver.connection() as conn:
        with_rows = await conn.fetch_all("SELECT a", None)
        without_rows = await conn.fetch_all("SELECT b", None)
    # then
    assert (with_rows, without_rows) == ([(7,)], [])


async def test_execute_returns_queued_exec_result():
    # given
    driver = RecordingDriver()
    queued = ExecResult(rowcount=5, lastrowid=9)
    driver.queue(queued)
    # when
    async with driver.connection() as conn:
        result = await conn.execute("UPDATE t SET a = 1", None)
    # then
    assert result == queued


async def test_execute_wraps_queued_rows_in_an_exec_result():
    # given
    driver = RecordingDriver()
    driver.queue([(1,), (2,)])
    # when
    async with driver.connection() as conn:
        result = await conn.execute("INSERT ... RETURNING id", None)
    # then
    assert result == ExecResult(rowcount=2, rows=[(1,), (2,)])


async def test_execute_many_returns_queued_exec_result():
    # given
    driver = RecordingDriver()
    driver.queue(ExecResult(rowcount=3))
    # when
    async with driver.connection() as conn:
        result = await conn.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,), (3,)])
    # then
    assert result == ExecResult(rowcount=3)


async def test_stream_yields_queued_rows_in_chunks():
    # given
    driver = RecordingDriver()
    driver.queue([(1,), (2,), (3,), (4,), (5,)])
    # when
    async with driver.connection() as conn:
        chunks = await stream_chunks(conn, "SELECT a", None, 2)
    # then
    assert chunks == [[(1,), (2,)], [(3,), (4,)], [(5,)]]


async def test_stream_of_no_rows_yields_no_chunks():
    # given
    driver = RecordingDriver()
    driver.queue([])
    # when
    async with driver.connection() as conn:
        chunks = await stream_chunks(conn, "SELECT a", None, 2)
    # then
    assert chunks == []


@pytest.mark.parametrize("operation", RESULT_OPERATIONS)
async def test_statement_without_queued_result_raises_no_queued_result(operation):
    # given
    driver = RecordingDriver()
    # when / then
    async with driver.connection() as conn:
        with pytest.raises(NoQueuedResult):
            await perform_operation(conn, operation)


def test_no_queued_result_is_a_lookup_error():
    # when / then
    assert issubclass(NoQueuedResult, LookupError)


async def test_queued_exception_is_raised_and_later_results_still_flow():
    # given
    driver = RecordingDriver()
    error = ValueError("boom")
    # when
    raised, rows = await queue_error_then_rows(driver, error)
    # then
    assert raised is error
    assert rows == [(1,)]


async def test_fetch_all_is_logged_with_sql_and_params():
    # given
    driver = RecordingDriver()
    driver.queue([(1,)])
    # when
    async with driver.connection() as conn:
        await conn.fetch_all("SELECT %s", (1,))
    # then
    assert driver.statements == [Statement("fetch_all", "SELECT %s", (1,))]


async def test_execute_is_logged_with_sql_and_params():
    # given
    driver = RecordingDriver()
    driver.queue(ExecResult(rowcount=1))
    # when
    async with driver.connection() as conn:
        await conn.execute("UPDATE t SET a = %s", (1,))
    # then
    assert driver.statements == [Statement("execute", "UPDATE t SET a = %s", (1,))]


async def test_execute_many_is_logged_with_sql_and_batch():
    # given
    driver = RecordingDriver()
    driver.queue(ExecResult(rowcount=2))
    # when
    async with driver.connection() as conn:
        await conn.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,)])
    # then
    assert driver.statements == [
        Statement("execute_many", "INSERT INTO t VALUES (%s)", [(1,), (2,)])
    ]


async def test_stream_is_logged_with_sql_and_params():
    # given
    driver = RecordingDriver()
    driver.queue([(1,)])
    # when
    async with driver.connection() as conn:
        await stream_chunks(conn, "SELECT a", (1,), 10)
    # then
    assert driver.statements == [Statement("stream", "SELECT a", (1,))]


@pytest.mark.parametrize("operation", ["begin", "commit", "rollback"])
async def test_transaction_control_is_logged_by_kind(operation):
    # given
    driver = RecordingDriver()
    # when
    async with driver.connection() as conn:
        await perform_operation(conn, operation)
    # then
    assert driver.statements == [Statement(operation)]


@pytest.mark.parametrize("operation", ["savepoint", "release_savepoint", "rollback_to_savepoint"])
async def test_savepoint_calls_are_logged_with_their_id(operation):
    # given
    driver = RecordingDriver()
    # when
    async with driver.connection() as conn:
        await perform_operation(conn, operation)
    # then
    assert driver.statements == [Statement(operation, "sp_1")]


async def test_statements_are_logged_in_call_order_across_connections():
    # given
    driver = RecordingDriver()
    # when
    await run_on_two_connections(driver)
    # then
    assert statement_kinds(driver) == ["begin", "commit"]


async def test_statement_that_finds_no_result_is_still_logged():
    # given
    driver = RecordingDriver()
    # when
    async with driver.connection() as conn:
        with pytest.raises(NoQueuedResult):
            await perform_operation(conn, "execute")
    # then
    assert statement_kinds(driver) == ["execute"]


def test_is_disconnect_is_false_by_default():
    # given
    driver = RecordingDriver()
    # when
    result = driver.is_disconnect(OSError("gone"))
    # then
    assert result is False


def test_is_disconnect_is_true_only_for_configured_types():
    # given
    driver = RecordingDriver()
    driver.disconnect_errors = (OSError,)
    # when
    results = (driver.is_disconnect(OSError("gone")), driver.is_disconnect(ValueError("x")))
    # then
    assert results == (True, False)
