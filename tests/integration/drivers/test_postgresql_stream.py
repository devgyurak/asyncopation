"""Streams: chunking, server-side cursor options and cleanup."""

import psycopg
import pytest
from django.db import DataError, InterfaceError, ProgrammingError

from tests.support.drivers import (
    POSTGRES_ONLY,
    flatten,
    held_cursors_after_close_failed_in_error_state,
    holdable_flags,
    nested_streams_left_cursors,
    observe_failed_stream,
    observe_stream,
    open_cursors,
    raw_begin_then_failing_stream,
    recover_from_failed_close_with_savepoint,
    release_with_open_stream,
    scrollable_flags,
    sizes,
    stream_chunks,
    stream_exit_after_savepoint_rollback,
    stream_failure_then_savepoint_rollback,
    stream_whose_close_fails_once,
    stream_with_close_all_in_body,
    use_stale_stream,
)

pytestmark = POSTGRES_ONLY

SERIES = "SELECT g FROM generate_series(1, 10) g ORDER BY g"


async def test_stream_yields_chunks_of_at_most_chunk_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 4)
    # then
    assert sizes(chunks) == [4, 4, 2]


async def test_stream_chunks_concatenate_to_fetch_all_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 3)
        rows = await conn.fetch_all(SERIES, None)
    # then
    assert flatten(chunks) == rows


async def test_stream_with_chunk_larger_than_result_yields_one_chunk(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 100)
    # then
    assert sizes(chunks) == [10]


async def test_stream_without_rows_yields_no_chunks(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, "SELECT 1 WHERE false", None, 5)
    # then
    assert chunks == []


async def test_stream_binds_parameters(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(
            conn, "SELECT g FROM generate_series(1, %s) g ORDER BY g", (5,), 2
        )
    # then
    assert flatten(chunks) == [(1,), (2,), (3,), (4,), (5,)]


async def test_stream_declares_non_scrollable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2)
    # then
    assert scrollable_flags(seen.cursors_open) == [False]


async def test_stream_in_autocommit_declares_holdable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2)
    # then
    assert holdable_flags(seen.cursors_open) == [True]


async def test_stream_inside_transaction_declares_non_holdable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, transactional=True)
    # then
    assert holdable_flags(seen.cursors_open) == [False]


async def test_stream_exit_closes_cursor_after_partial_read(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, reads=1)
    # then
    assert len(seen.cursors_open) == 1
    assert seen.cursors_after == []


async def test_stream_exit_closes_cursor_inside_transaction(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, transactional=True)
    # then
    assert seen.cursors_after == []


async def test_stream_closes_cursor_when_body_raises(driver):
    # given
    async with driver.connection() as conn:
        # when
        outcome = await observe_failed_stream(conn, SERIES, 2)
    # then
    assert isinstance(outcome.error, RuntimeError)
    assert outcome.cursors_after == []


async def test_stream_leaves_connection_reusable(driver):
    # given
    async with driver.connection() as conn:
        await observe_stream(conn, SERIES, None, 2)
        # when
        rows = await conn.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


async def test_stream_binding_mode_selects_cursor_statement_text(driver, binding):
    # given
    sql = "SELECT g FROM generate_series(1, %s) g"
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, sql, (5,), 2)
    # then
    assert ("$1" in seen.cursors_open[0].statement) is binding.server_side


async def test_no_cursor_is_open_on_a_fresh_connection(driver):
    # given
    async with driver.connection() as conn:
        # when
        cursors = await open_cursors(conn)
    # then
    assert cursors == []


async def test_stream_without_server_side_cursors_declares_no_cursor(no_cursor_driver):
    # given
    async with no_cursor_driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 4)
    # then
    assert seen.cursors_open == []


async def test_stream_without_server_side_cursors_yields_the_same_chunks(no_cursor_driver):
    # given
    async with no_cursor_driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 4)
    # then
    assert sizes(chunks) == [4, 4, 2]


async def test_capabilities_report_disabled_server_side_cursors(no_cursor_driver):
    # when
    capabilities = no_cursor_driver.capabilities
    # then
    assert capabilities.server_side_cursors is False


TRANSACTIONAL = pytest.mark.parametrize(
    "transactional", [False, True], ids=["autocommit-withhold", "transaction"]
)
STALE_USE = pytest.mark.parametrize("close", [False, True], ids=["iterate", "close"])


@TRANSACTIONAL
async def test_stream_left_open_at_release_leaves_no_cursor_for_the_next_borrower(
    single_driver, transactional
):
    # when
    outcome = await release_with_open_stream(single_driver, transactional=transactional)
    # then
    assert outcome.cursors == []


@TRANSACTIONAL
async def test_stream_left_open_at_release_keeps_the_same_backend(single_driver, transactional):
    # when
    outcome = await release_with_open_stream(single_driver, transactional=transactional)
    # then
    assert outcome.pid_after == outcome.pid_before


@TRANSACTIONAL
async def test_iterating_a_stream_after_its_borrow_ended_raises_interface_error(
    single_driver, transactional
):
    # when
    outcome = await use_stale_stream(single_driver, close=False, transactional=transactional)
    # then
    assert type(outcome.stale_error) is InterfaceError


@TRANSACTIONAL
async def test_closing_a_stream_after_its_borrow_ended_is_a_no_op(single_driver, transactional):
    # when
    outcome = await use_stale_stream(single_driver, close=True, transactional=transactional)
    # then
    assert outcome.stale_error is None


@STALE_USE
@TRANSACTIONAL
async def test_stale_stream_does_not_disturb_the_next_borrowers_transaction(
    single_driver, close, transactional
):
    # when
    outcome = await use_stale_stream(single_driver, close=close, transactional=transactional)
    # then
    assert outcome.b_error is None
    assert outcome.b_rows == [(2,)]


@STALE_USE
@TRANSACTIONAL
async def test_stale_stream_does_not_change_the_next_borrowers_backend(
    single_driver, close, transactional
):
    # when
    outcome = await use_stale_stream(single_driver, close=close, transactional=transactional)
    # then
    assert outcome.pid_after == outcome.pid_before


async def test_failed_close_of_a_held_cursor_leaves_none_for_the_next_borrower(single_driver):
    # when
    cursors = await held_cursors_after_close_failed_in_error_state(single_driver)
    # then
    assert cursors == []


async def test_stream_failing_under_a_savepoint_recovers_after_rollback_to_savepoint(
    single_driver,
):
    # when
    outcome = await stream_failure_then_savepoint_rollback(single_driver)
    # then
    assert type(outcome.error) is DataError
    assert outcome.second_rows == [(1,), (2,), (3,)]
    assert outcome.resource_warnings == 0


@pytest.mark.parametrize("exit_inner_first", [True, False], ids=["inner-first", "outer-first"])
async def test_second_stream_on_one_connection_does_not_leak_the_first_cursor(
    single_driver, exit_inner_first
):
    # when
    cursors = await nested_streams_left_cursors(single_driver, exit_inner_first=exit_inner_first)
    # then
    assert cursors == []


async def test_stream_error_after_a_raw_begin_maps_to_data_error(single_driver):
    # when
    outcome = await raw_begin_then_failing_stream(single_driver)
    # then
    assert type(outcome.error) is DataError


async def test_stream_error_after_a_raw_begin_keeps_the_healthy_connection(single_driver):
    # when
    outcome = await raw_begin_then_failing_stream(single_driver)
    # then
    assert outcome.pid_after == outcome.pid_before


async def test_stream_error_after_a_raw_begin_leaves_no_held_cursor(single_driver):
    # when
    outcome = await raw_begin_then_failing_stream(single_driver)
    # then
    assert outcome.cursors_after == []
    assert outcome.resource_warnings == 0


async def test_stream_exit_after_the_savepoint_was_rolled_back_raises_a_django_error(
    single_driver,
):
    # when
    outcome = await stream_exit_after_savepoint_rollback(single_driver)
    # then
    assert type(outcome.error) is ProgrammingError
    assert isinstance(outcome.error.__cause__, psycopg.errors.InvalidCursorName)


async def test_failing_close_inside_a_transaction_raises_a_django_error(single_driver):
    # when
    outcome = await stream_with_close_all_in_body(single_driver, transactional=True)
    # then
    assert type(outcome.error) is ProgrammingError
    assert isinstance(outcome.error.__cause__, psycopg.errors.InvalidCursorName)


async def test_rollback_after_a_failing_close_in_a_transaction_succeeds(single_driver):
    # when
    outcome = await stream_with_close_all_in_body(single_driver, transactional=True)
    # then
    assert outcome.rollback_error is None
    assert outcome.resource_warnings == 0


async def test_rollback_after_a_failing_close_and_a_savepoint_rollback_succeeds(single_driver):
    # when
    outcome = await stream_exit_after_savepoint_rollback(single_driver)
    # then
    assert outcome.rollback_error is None
    assert outcome.resource_warnings == 0


async def test_failing_close_in_transaction_leaves_it_recoverable_by_rollback_to_savepoint(
    single_driver,
):
    # when
    outcome = await recover_from_failed_close_with_savepoint(single_driver)
    # then
    assert type(outcome.close_error) is ProgrammingError
    assert outcome.savepoint_error is None
    assert outcome.fetch_error is None
    assert outcome.rows == [(1,)]
    assert outcome.pid_after == outcome.pid_before
    assert outcome.resource_warnings == 0


async def test_failing_close_in_autocommit_is_swallowed(single_driver):
    # when
    outcome = await stream_with_close_all_in_body(single_driver, transactional=False)
    # then
    assert outcome.error is None


async def test_failing_close_in_autocommit_keeps_the_connection_and_leaves_no_cursor(
    single_driver,
):
    # when
    outcome = await stream_with_close_all_in_body(single_driver, transactional=False)
    # then
    assert outcome.pid_after == outcome.pid_before
    assert outcome.cursors_after == []
    assert outcome.resource_warnings == 0


@TRANSACTIONAL
async def test_body_error_wins_over_a_failing_close(single_driver, transactional):
    # when
    outcome = await stream_with_close_all_in_body(
        single_driver, transactional=transactional, body_error=True
    )
    # then
    assert type(outcome.error) is RuntimeError


async def test_close_error_in_autocommit_is_retried_by_release_on_the_same_backend(single_driver):
    # when
    outcome = await stream_whose_close_fails_once(
        single_driver, psycopg.errors.OperationalError("close failed"), transactional=False
    )
    # then
    assert outcome.exit_error is None
    assert outcome.pid_after == outcome.pid_before
    assert outcome.cursors_after == []


async def test_close_error_in_autocommit_leaks_no_cursor_object(single_driver):
    # when
    outcome = await stream_whose_close_fails_once(
        single_driver, psycopg.errors.OperationalError("close failed"), transactional=False
    )
    # then
    assert outcome.resource_warnings == 0


async def test_non_cursor_close_error_on_a_held_cursor_in_a_transaction_leaves_no_cursor(
    single_driver,
):
    # when
    outcome = await stream_whose_close_fails_once(
        single_driver, psycopg.errors.QueryCanceled("simulated"), transactional=True
    )
    # then
    assert outcome.cursors_after == []
