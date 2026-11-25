"""Executors against a real PostgreSQL: concurrency, pinning, owner rule, broken transactions,
streams and cancellation while waiting for a pooled connection (ADR-0001 Validation)."""

import pytest
from django.db import OperationalError
from django.db.transaction import TransactionManagementError

from asyncopation import ConcurrentQueryError
from asyncopation.drivers.base import PoolStats
from harness.settings import VENDOR
from tests.support.executors.postgres import (
    break_out_of_a_pinned_stream,
    cancel_a_pin_waiting_for_a_connection,
    cancel_pin_mid_statement,
    cancel_racing_the_release,
    cancel_while_waiting_for_a_connection,
    child_queries_inside_transaction,
    close_and_count_sessions,
    close_while_a_transaction_is_pinned,
    create_task_child_after_exit,
    deadline_inside_transaction,
    deadline_outside_transaction,
    gather_sleeping_queries,
    leave_pin_with_open_stream,
    observe_gathered_queries_on_the_server,
    pinned_backends,
    savepoint_from_taskgroup_child,
    terminate_pinned_backend,
)

pytestmark = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="MySQL/MariaDB drivers (asyncmy) arrive with issue #7; the executor integration "
    "tests run on PostgreSQL only until then",
)

CHILDREN = pytest.mark.parametrize("taskgroup", [False, True], ids=["gather", "taskgroup"])
# Race probes, not exact interleavings: the delays only make a given order likely. What every
# case asserts holds for any order (nothing leaks), so a different interleaving still passes.
RACES = pytest.mark.parametrize(
    ("delay", "cancel_first"),
    [
        pytest.param(0, True, id="cancel-then-release"),
        pytest.param(0, False, id="release-then-cancel-same-tick"),
        pytest.param(0.0005, False, id="cancel-0.5ms-after-release"),
        pytest.param(0.002, False, id="cancel-2ms-after-release"),
        pytest.param(0.01, False, id="cancel-10ms-after-release"),
    ],
)


# --------------------------------------------------------------------------- borrowed
async def test_gathered_queries_run_on_separate_backends(spy):
    # when
    outcome = await gather_sleeping_queries(4, 0.5)
    # then
    assert outcome.backends == 4


async def test_gathered_queries_overlap_on_the_server(spy, app_name):
    # when
    outcome = await observe_gathered_queries_on_the_server(app_name, 3)
    # then
    assert outcome.active_on_server == 3


async def test_gathered_queries_cancelled_while_overlapping_leak_nothing(spy, app_name):
    # when
    outcome = await observe_gathered_queries_on_the_server(app_name, 3)
    # then
    assert outcome.all_cancelled is True
    assert (outcome.in_flight, outcome.pool.in_use, outcome.pool.waiting) == (0, 0, 0)


async def test_gathered_queries_leave_nothing_on_the_server_after_close(spy, app_name):
    # given
    await gather_sleeping_queries(3, 0.2)
    # when
    error, sessions = await close_and_count_sessions(app_name)
    # then
    assert (error, sessions) == (None, 0)


# --------------------------------------------------------------------------- pinned
async def test_a_pin_reuses_one_backend_for_all_its_statements(spy):
    # when
    pids = await pinned_backends(4)
    # then
    assert len(set(pids)) == 1


async def test_cancelling_the_owner_mid_statement_cancels_the_pin_task(spy, app_name):
    # when
    outcome = await cancel_pin_mid_statement(app_name)
    # then
    assert outcome.cancelled is True


async def test_cancelling_the_owner_mid_statement_releases_the_connection_and_the_unit(
    spy, app_name
):
    # when
    outcome = await cancel_pin_mid_statement(app_name)
    # then
    assert outcome.in_flight == 0
    assert (outcome.pool.in_use, outcome.pool.waiting) == (0, 0)


async def test_cancelling_an_autocommit_pin_does_not_break_it(spy, app_name):
    # when
    outcome = await cancel_pin_mid_statement(app_name)
    # then
    assert outcome.broken is False


async def test_cancelling_the_owner_leaves_no_session_behind_after_close(spy, app_name):
    # given
    await cancel_pin_mid_statement(app_name)
    # when
    error, sessions = await close_and_count_sessions(app_name)
    # then
    assert (error, sessions) == (None, 0)


# --------------------------------------------------------------------------- owner rule
@CHILDREN
async def test_a_child_querying_inside_a_transaction_raises_concurrent_query_error(spy, taskgroup):
    # when
    outcome = await child_queries_inside_transaction(taskgroup=taskgroup)
    # then
    assert isinstance(outcome.error, ConcurrentQueryError)


@CHILDREN
async def test_the_refused_child_leaves_the_owners_transaction_open_and_healthy(spy, taskgroup):
    # when
    outcome = await child_queries_inside_transaction(taskgroup=taskgroup)
    # then
    assert (outcome.in_transaction_before, outcome.broken) == (True, False)


@CHILDREN
async def test_the_owners_transaction_still_rolls_back_cleanly(spy, taskgroup):
    # when
    outcome = await child_queries_inside_transaction(taskgroup=taskgroup)
    # then
    assert (outcome.rollback_error, outcome.in_transaction_after) == (None, False)
    assert outcome.pid_stable is True


async def test_a_savepoint_opened_from_a_taskgroup_child_raises(spy):
    # when
    error = await savepoint_from_taskgroup_child()
    # then
    assert isinstance(error, ConcurrentQueryError)


async def test_a_create_task_child_querying_after_the_block_exited_raises(spy):
    # when
    error = await create_task_child_after_exit()
    # then
    assert isinstance(error, ConcurrentQueryError)


async def test_a_refused_use_after_the_block_takes_no_connection_from_the_pool(spy, app_name):
    # given
    await create_task_child_after_exit()
    # when
    error, sessions = await close_and_count_sessions(app_name)
    # then
    assert (error, sessions) == (None, 0)


# --------------------------------------------------------------------------- broken transaction
async def test_a_statement_cancelled_in_a_transaction_marks_the_pin_broken(spy):
    # when
    outcome = await deadline_inside_transaction()
    # then
    assert (outcome.timed_out, outcome.broken) == (True, True)


async def test_the_next_statement_of_a_broken_pin_raises_transaction_management_error(spy):
    # when
    outcome = await deadline_inside_transaction()
    # then
    assert isinstance(outcome.next_error, TransactionManagementError)


async def test_rollback_on_the_broken_pin_is_a_no_op(spy):
    # when
    outcome = await deadline_inside_transaction()
    # then
    assert outcome.rollback_error is None


async def test_leaving_the_broken_pin_discards_the_connection(spy):
    # when
    outcome = await deadline_inside_transaction()
    # then
    assert outcome.backend_ended is True


async def test_the_pool_settles_after_the_broken_pin_was_left(spy):
    # when
    outcome = await deadline_inside_transaction()
    # then
    assert (outcome.pool.in_use, outcome.pool.waiting, outcome.in_flight) == (0, 0, 0)


async def test_a_statement_cancelled_without_a_transaction_does_not_break_the_pin(spy):
    # when
    timed_out, next_error, broken = await deadline_outside_transaction()
    # then
    assert (timed_out, next_error, broken) == (True, None, False)


async def test_a_stream_read_cancelled_in_a_transaction_marks_the_pin_broken(spy):
    # when
    outcome = await deadline_inside_transaction(stream=True)
    # then
    assert (outcome.timed_out, outcome.broken) == (True, True)


async def test_the_next_statement_after_a_cancelled_stream_read_is_refused(spy):
    # when
    outcome = await deadline_inside_transaction(stream=True)
    # then
    assert isinstance(outcome.next_error, TransactionManagementError)


async def test_leaving_the_pin_after_a_cancelled_stream_read_discards_the_connection(spy):
    # when
    outcome = await deadline_inside_transaction(stream=True)
    # then
    assert outcome.backend_ended is True


async def test_the_pool_settles_after_a_cancelled_stream_read(spy):
    # when
    outcome = await deadline_inside_transaction(stream=True)
    # then
    assert (outcome.pool.in_use, outcome.pool.waiting, outcome.in_flight) == (0, 0, 0)


async def test_a_terminated_backend_surfaces_as_a_django_operational_error(spy):
    # when
    outcome = await terminate_pinned_backend()
    # then
    assert outcome.terminated is True
    assert isinstance(outcome.next_error, OperationalError)


async def test_a_terminated_backend_marks_the_pin_broken(spy):
    # when
    outcome = await terminate_pinned_backend()
    # then
    assert outcome.broken is True


async def test_rollback_and_the_pin_exit_after_a_terminated_backend_raise_nothing(spy):
    # when
    outcome = await terminate_pinned_backend()
    # then
    assert (outcome.rollback_error, outcome.exit_error) == (None, None)


async def test_the_pool_replaces_a_terminated_backend_and_nothing_stays_in_flight(spy):
    # when
    outcome = await terminate_pinned_backend()
    # then
    assert (outcome.pool.in_use, outcome.pool.waiting, outcome.in_flight) == (0, 0, 0)


async def test_close_gives_up_on_an_open_pin_transaction_with_a_timeout_error(spy, app_name):
    # when
    outcome = await close_while_a_transaction_is_pinned(0.3, app_name)
    # then
    assert isinstance(outcome.close_error, TimeoutError)


async def test_a_statement_on_a_pin_close_gave_up_on_raises_concurrent_query_error(spy, app_name):
    # when
    outcome = await close_while_a_transaction_is_pinned(0.3, app_name)
    # then
    assert isinstance(outcome.statement_error, ConcurrentQueryError)
    assert "drivers were closed by close()" in str(outcome.statement_error)


async def test_leaving_a_pin_after_close_gave_up_raises_nothing_and_ends_the_backend(spy, app_name):
    # when
    outcome = await close_while_a_transaction_is_pinned(0.3, app_name)
    # then
    assert outcome.exit_error is None
    assert outcome.backend_ended is True
    assert outcome.in_flight == 0


async def test_no_session_is_left_after_leaving_a_pin_close_gave_up_on(spy, app_name):
    # when
    outcome = await close_while_a_transaction_is_pinned(0.3, app_name)
    # then
    assert outcome.sessions == 0


# --------------------------------------------------------------------------- streams
async def test_leaving_a_stream_midway_inside_a_transaction_leaks_nothing(spy):
    # when
    outcome = await break_out_of_a_pinned_stream()
    # then
    assert outcome.first_chunk == [(1,), (2,)]
    assert outcome.in_flight == 0
    assert (outcome.pool.in_use, outcome.pool.waiting) == (0, 0)


async def test_reading_a_stream_after_its_block_ended_raises(spy):
    # when
    outcome = await break_out_of_a_pinned_stream()
    # then
    assert isinstance(outcome.read_after_block, ConcurrentQueryError)


async def test_reading_a_stream_after_the_pin_ended_raises(spy):
    # when
    outcome = await break_out_of_a_pinned_stream()
    # then
    assert isinstance(outcome.read_after_pin, ConcurrentQueryError)


async def test_leaving_the_pin_with_an_open_stream_closes_it_and_leaks_nothing(spy):
    # when
    read_error, in_flight, pool = await leave_pin_with_open_stream()
    # then
    assert isinstance(read_error, ConcurrentQueryError)
    assert in_flight == 0
    assert pool.in_use == 0


async def test_a_stream_left_open_at_pin_exit_leaves_no_session_after_close(spy, app_name):
    # given
    await leave_pin_with_open_stream()
    # when
    error, sessions = await close_and_count_sessions(app_name)
    # then
    assert (error, sessions) == (None, 0)


# --------------------------------------------------------------------------- pool waiters
async def test_cancelling_a_borrower_waiting_for_a_connection_cancels_its_task(tight_spy):
    # when
    outcome = await cancel_while_waiting_for_a_connection()
    # then
    assert outcome.cancelled is True


async def test_a_cancelled_waiting_borrower_stops_counting_as_in_flight(tight_spy):
    # when
    outcome = await cancel_while_waiting_for_a_connection()
    # then
    assert outcome.in_flight_after_cancel == 1  # only the stream that holds the connection
    assert outcome.in_flight == 0


async def test_a_cancelled_waiting_borrower_leaks_no_connection(tight_spy):
    # when
    outcome = await cancel_while_waiting_for_a_connection()
    # then
    assert outcome.pool == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.fresh_rows == [(1,)]


async def test_a_pin_cancelled_while_waiting_for_a_connection_is_cancelled(tight_spy):
    # when
    outcome = await cancel_a_pin_waiting_for_a_connection()
    # then
    assert outcome.cancelled is True


async def test_a_cancelled_waiting_pin_stops_counting_as_in_flight(tight_spy):
    # when
    outcome = await cancel_a_pin_waiting_for_a_connection()
    # then
    assert outcome.in_flight_after_cancel == 1  # only the stream that holds the connection
    assert outcome.in_flight == 0


async def test_a_cancelled_waiting_pin_leaks_no_connection(tight_spy):
    # when
    outcome = await cancel_a_pin_waiting_for_a_connection()
    # then
    assert outcome.pool == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.fresh_rows == [(1,)]


@RACES
async def test_a_cancel_racing_the_acquisition_returns_the_connection(
    tight_spy, delay, cancel_first
):
    # when
    outcome = await cancel_racing_the_release(delay, cancel_first=cancel_first)
    # then
    assert outcome.pool.in_use == 0
    assert outcome.fresh_rows == [(1,)]


@RACES
async def test_a_cancel_racing_the_acquisition_leaves_nothing_in_flight(
    tight_spy, delay, cancel_first
):
    # when
    outcome = await cancel_racing_the_release(delay, cancel_first=cancel_first)
    # then
    assert (outcome.in_flight, outcome.pool.waiting) == (0, 0)
