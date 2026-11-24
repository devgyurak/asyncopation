import pytest
from django.db import InterfaceError

from asyncopation import testing
from asyncopation.drivers.base import Capabilities, ExecResult
from asyncopation.testing import NoQueuedResult, RecordingDriver, Statement
from tests.support.drivers import (
    as_connection,
    as_driver,
    error_of_call,
    kinds_after_leaving,
    logged_params,
    next_rows_after_commit,
    perform_operation,
    queue_error_then_rows,
    recording_stale_chunk_error,
    recording_statements_snapshot,
    released_connection_error,
    run_on_two_connections,
    statement_kinds,
    stream_chunks,
)

RESULT_OPERATIONS = ["fetch_all", "execute", "execute_many", "stream"]
RELEASED_OPERATIONS = [
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


def test_recording_driver_satisfies_the_driver_protocol():
    # given
    driver = RecordingDriver()
    # when
    accepted = as_driver(driver)
    # then
    assert accepted is driver


async def test_recording_connection_satisfies_the_connection_protocol(driver):
    # when
    async with driver.connection() as conn:
        accepted = as_connection(conn)
    # then
    assert accepted is conn


def test_testing_package_reexports_the_driver_vocabulary():
    # when / then
    assert testing.Capabilities is Capabilities
    assert testing.ExecResult is ExecResult


def test_capabilities_before_open_raise_runtime_error():
    # given
    driver = RecordingDriver()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_connection_before_open_raises_runtime_error():
    # given
    driver = RecordingDriver()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


async def test_capabilities_after_close_raise_runtime_error():
    # given
    driver = RecordingDriver()
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_connection_after_close_raises_runtime_error():
    # given
    driver = RecordingDriver()
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


async def test_default_capabilities_describe_a_modern_server(driver):
    # when
    capabilities = driver.capabilities
    # then
    assert capabilities == Capabilities(
        server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True
    )


async def test_custom_capabilities_are_reported():
    # given
    custom = Capabilities(
        server_version=(11, 8), server_side_cursors=False, atomic_execute_many=False
    )
    driver = RecordingDriver(capabilities=custom)
    # when
    await driver.open()
    # then
    assert driver.capabilities == custom


def test_new_driver_is_neither_opened_nor_closed():
    # when
    driver = RecordingDriver()
    # then
    assert (driver.opened, driver.closed) == (False, False)


async def test_open_marks_the_driver_opened():
    # given
    driver = RecordingDriver()
    # when
    await driver.open()
    # then
    assert (driver.opened, driver.closed) == (True, False)


async def test_close_marks_the_driver_closed():
    # given
    driver = RecordingDriver()
    await driver.open()
    # when
    await driver.close()
    # then
    assert driver.closed is True


async def test_fetch_all_pops_queued_rows_in_fifo_order(driver):
    # given
    driver.queue([(1,)], [(2,), (3,)])
    # when
    async with driver.connection() as conn:
        first = await conn.fetch_all("SELECT a", None)
        second = await conn.fetch_all("SELECT b", None)
    # then
    assert (first, second) == ([(1,)], [(2,), (3,)])


async def test_fetch_all_uses_rows_of_a_queued_exec_result(driver):
    # given
    driver.queue(ExecResult(rowcount=1, rows=[(7,)]), ExecResult(rowcount=0))
    # when
    async with driver.connection() as conn:
        with_rows = await conn.fetch_all("SELECT a", None)
        without_rows = await conn.fetch_all("SELECT b", None)
    # then
    assert (with_rows, without_rows) == ([(7,)], [])


async def test_execute_returns_queued_exec_result(driver):
    # given
    queued = ExecResult(rowcount=5, lastrowid=9)
    driver.queue(queued)
    # when
    async with driver.connection() as conn:
        result = await conn.execute("UPDATE t SET a = 1", None)
    # then
    assert result == queued


async def test_execute_wraps_queued_rows_in_an_exec_result(driver):
    # given
    driver.queue([(1,), (2,)])
    # when
    async with driver.connection() as conn:
        result = await conn.execute("INSERT ... RETURNING id", None)
    # then
    assert result == ExecResult(rowcount=2, rows=[(1,), (2,)])


async def test_execute_many_returns_queued_exec_result(driver):
    # given
    driver.queue(ExecResult(rowcount=3))
    # when
    async with driver.connection() as conn:
        result = await conn.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,), (3,)])
    # then
    assert result == ExecResult(rowcount=3)


async def test_stream_yields_queued_rows_in_chunks(driver):
    # given
    driver.queue([(1,), (2,), (3,), (4,), (5,)])
    # when
    async with driver.connection() as conn:
        chunks = await stream_chunks(conn, "SELECT a", None, 2)
    # then
    assert chunks == [[(1,), (2,)], [(3,), (4,)], [(5,)]]


async def test_stream_of_no_rows_yields_no_chunks(driver):
    # given
    driver.queue([])
    # when
    async with driver.connection() as conn:
        chunks = await stream_chunks(conn, "SELECT a", None, 2)
    # then
    assert chunks == []


@pytest.mark.parametrize("operation", RESULT_OPERATIONS)
async def test_statement_without_queued_result_raises_no_queued_result(driver, operation):
    # when
    error = await error_of_call(driver, operation)
    # then
    assert isinstance(error, NoQueuedResult)


def test_no_queued_result_is_a_lookup_error():
    # when / then
    assert issubclass(NoQueuedResult, LookupError)


async def test_queued_exception_is_raised_and_later_results_still_flow(driver):
    # given
    error = ValueError("boom")
    # when
    raised, rows = await queue_error_then_rows(driver, error)
    # then
    assert raised is error
    assert rows == [(1,)]


async def test_fetch_all_is_logged_with_sql_and_params(driver):
    # given
    driver.queue([(1,)])
    # when
    async with driver.connection() as conn:
        await conn.fetch_all("SELECT %s", (1,))
    # then
    assert driver.statements == [Statement("fetch_all", "SELECT %s", (1,))]


async def test_execute_is_logged_with_sql_and_params(driver):
    # given
    driver.queue(ExecResult(rowcount=1))
    # when
    async with driver.connection() as conn:
        await conn.execute("UPDATE t SET a = %s", (1,))
    # then
    assert driver.statements == [Statement("execute", "UPDATE t SET a = %s", (1,))]


async def test_execute_many_is_logged_with_sql_and_batch(driver):
    # given
    driver.queue(ExecResult(rowcount=2))
    # when
    async with driver.connection() as conn:
        await conn.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,)])
    # then
    assert driver.statements == [
        Statement("execute_many", "INSERT INTO t VALUES (%s)", [(1,), (2,)])
    ]


async def test_stream_is_logged_with_sql_and_params(driver):
    # given
    driver.queue([(1,)])
    # when
    async with driver.connection() as conn:
        await stream_chunks(conn, "SELECT a", (1,), 10)
    # then
    assert driver.statements == [Statement("stream", "SELECT a", (1,))]


@pytest.mark.parametrize(
    ("operations", "expected"),
    [
        pytest.param(["commit"], ["commit"], id="commit"),
        pytest.param(["rollback"], ["rollback"], id="rollback"),
        pytest.param(["begin", "commit"], ["begin", "commit"], id="begin-commit"),
        pytest.param(["begin", "rollback"], ["begin", "rollback"], id="begin-rollback"),
    ],
)
async def test_transaction_control_is_logged_by_kind(driver, operations, expected):
    # when
    kinds = await kinds_after_leaving(driver, *operations)
    # then
    assert kinds == expected


async def test_leaving_the_connection_inside_a_transaction_logs_a_rollback(driver):
    # when
    kinds = await kinds_after_leaving(driver, "begin")
    # then
    assert kinds == ["begin", "rollback"]


async def test_leaving_after_commit_does_not_log_an_extra_rollback(driver):
    # when
    kinds = await kinds_after_leaving(driver, "begin", "commit")
    # then
    assert kinds == ["begin", "commit"]


@pytest.mark.parametrize("operation", ["savepoint", "release_savepoint", "rollback_to_savepoint"])
async def test_savepoint_calls_are_logged_with_their_id(driver, operation):
    # when
    async with driver.connection() as conn:
        await perform_operation(conn, operation)
    # then
    assert driver.statements == [Statement(operation, "sp_1")]


@pytest.mark.parametrize("operation", ["commit", "rollback"])
async def test_commit_and_rollback_raise_a_queued_exception_and_still_log(driver, operation):
    # given
    error = RuntimeError("commit failed")
    driver.queue(error)
    # when
    raised = await error_of_call(driver, operation)
    # then
    assert raised is error
    assert statement_kinds(driver) == [operation]


@pytest.mark.parametrize("operation", ["commit", "rollback"])
async def test_commit_and_rollback_leave_queued_rows_untouched(driver, operation):
    # given
    driver.queue([(7,)])
    # when
    rows = await next_rows_after_commit(driver, operation)
    # then
    assert rows == [(7,)]


async def test_statements_are_logged_in_call_order_across_connections(driver):
    # when
    await run_on_two_connections(driver)
    # then
    assert statement_kinds(driver) == ["begin", "commit", "begin", "rollback"]


async def test_statement_that_finds_no_result_is_still_logged(driver):
    # when
    await error_of_call(driver, "execute")
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


@pytest.mark.parametrize("operation", RELEASED_OPERATIONS)
async def test_released_connection_refuses_every_operation(driver, operation):
    # when
    error = await released_connection_error(driver, operation)
    # then
    assert type(error) is InterfaceError


async def test_logged_params_do_not_change_when_the_caller_mutates_its_list():
    # given
    driver = RecordingDriver()
    await driver.open()
    driver.queue([(1,)])
    params = [1]
    async with driver.connection() as conn:
        await conn.fetch_all("SELECT %s", params)
    # when
    params.append(2)
    # then
    assert logged_params(driver) == [1]


async def test_logged_params_do_not_change_when_the_caller_mutates_its_dict():
    # given
    driver = RecordingDriver()
    await driver.open()
    driver.queue([(1,)])
    params = {"a": 1}
    async with driver.connection() as conn:
        await conn.fetch_all("SELECT %(a)s", params)
    # when
    params["b"] = 2
    # then
    assert logged_params(driver) == {"a": 1}


async def test_statements_returns_a_snapshot_the_caller_cannot_use_to_rewrite_history(driver):
    # when
    cleared, kinds_now = await recording_statements_snapshot(driver)
    # then
    assert cleared == ["begin"]
    assert kinds_now == ["begin", "commit"]


async def test_chunk_iterator_kept_after_the_borrow_raises_interface_error(driver):
    # when
    error = await recording_stale_chunk_error(driver)
    # then
    assert type(error) is InterfaceError
