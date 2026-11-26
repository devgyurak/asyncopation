"""ADR-0005 Decision 6 (issue #4): scoped observers follow the task tree, global observers see
every task, and a failing observer never fails the query."""

import asyncio
import logging

import pytest

from tests.support.hooks.events import (
    BOTH,
    FINISHED,
    STARTED,
    Failing,
    Raising,
    ReturnsCoroutine,
    ReturnsFuture,
    error_messages,
    logged_failure_messages,
    logged_failures,
)
from tests.support.hooks.lifetime import (
    aborted_by_started_observer,
    block_exited_in_a_gather_child,
    bound_method_registration,
    child_block_open_while_parent_runs,
    exit_out_of_order,
    generator_block_closed_by_another_task,
    observer_raising,
    observer_returning_an_awaitable,
    remove_keeps_the_older_registration,
    reversed_release,
    same_observer_added_twice,
)
from tests.support.hooks.scenarios import (
    child_of_observed_parent,
    child_starting_after_the_block,
    global_and_scoped_order,
    global_removed,
    global_seen_by_older_task,
    nested_blocks,
    observer_of_a_child,
    observer_of_a_sibling,
    scoped_order,
    statement_after_the_block,
    with_failing_observer,
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
SCOPES = pytest.mark.parametrize("scope", ["scoped", "global"])
FAILING_ON = pytest.mark.parametrize("on", [STARTED, FINISHED])
LOGGER = "asyncopation.connection"


# --------------------------------------------------------------------------- scoped observers
async def test_a_gather_child_is_observed_by_the_parent_scope(execenv):
    # when
    recorder = await child_of_observed_parent()
    # then
    assert recorder.sqls == ["SELECT 'child'"]


async def test_an_observer_registered_in_a_child_does_not_see_the_parent(execenv):
    # when
    recorder = await observer_of_a_child()
    # then
    assert recorder.sqls == ["SELECT 'child'"]


async def test_an_observer_registered_in_a_child_does_not_see_a_sibling(execenv):
    # when
    recorder = await observer_of_a_sibling()
    # then
    assert recorder.sqls == ["SELECT 'a'"]


async def test_a_statement_after_the_observe_block_is_not_observed(execenv):
    # when
    recorder = await statement_after_the_block()
    # then
    assert recorder.sqls == ["SELECT 'inside'"]


async def test_a_child_task_starting_its_statement_after_the_block_is_not_observed(execenv):
    # when
    recorder = await child_starting_after_the_block()
    # then
    assert recorder.events == []


async def test_a_nested_observe_block_sees_only_its_own_statements(execenv):
    # when
    nested = await nested_blocks()
    # then
    assert nested.inner.sqls == ["SELECT 'inner'"]


async def test_the_outer_observer_is_restored_after_a_nested_block(execenv):
    # when
    nested = await nested_blocks()
    # then
    assert nested.outer.sqls == ["SELECT 'inner'", "SELECT 'outer'"]


async def test_scoped_observers_are_called_in_registration_order(execenv):
    # when
    log = await scoped_order()
    # then
    assert log == [
        "first:query_started",
        "second:query_started",
        "first:query_finished",
        "second:query_finished",
    ]


async def test_an_observer_registered_in_a_child_does_not_see_a_concurrent_parent_statement(
    execenv,
):
    # when
    child, parent = await child_block_open_while_parent_runs()
    # then
    assert child.sqls == ["SELECT 'child'"]
    assert parent.sqls == ["SELECT 'parent'", "SELECT 'child'"]


async def test_blocks_exited_out_of_order_each_stop_at_their_own_exit(execenv):
    # when
    blocks = await exit_out_of_order()
    # then
    assert blocks.a.sqls == []
    assert blocks.b.sqls == ["SELECT 'a-exited'"]


FOREIGN_EXITS = pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(generator_block_closed_by_another_task, id="async-generator-aclose"),
        pytest.param(block_exited_in_a_gather_child, id="exit-in-gather-child"),
    ],
)


@FOREIGN_EXITS
async def test_a_block_exited_in_another_context_does_not_raise(execenv, scenario):
    # when
    outcome = await scenario()
    # then
    assert outcome.error is None


@FOREIGN_EXITS
async def test_a_block_exited_in_another_context_stops_reporting_to_the_entering_task(
    execenv, scenario
):
    # when
    outcome = await scenario()
    # then
    assert outcome.exited.sqls == ["SELECT 'open'"]


@FOREIGN_EXITS
async def test_a_block_exited_in_another_context_is_pruned_from_the_entering_context(
    execenv, scenario
):
    # when
    outcome = await scenario()
    # then
    assert outcome.still_held is False


@FOREIGN_EXITS
async def test_a_block_exited_in_another_context_leaves_the_other_registrations(execenv, scenario):
    # when
    outcome = await scenario()
    # then
    assert outcome.other.sqls == ["SELECT 'open'", "SELECT 'closed'"]


# --------------------------------------------------------------------------- global observers
async def test_a_global_observer_sees_a_task_created_before_it_was_added(execenv):
    # when
    recorder = await global_seen_by_older_task()
    # then
    assert recorder.sqls == ["SELECT 'task'"]


async def test_a_removed_global_observer_sees_no_later_statement(execenv):
    # when
    recorder = await global_removed()
    # then
    assert recorder.sqls == ["SELECT 'before'"]


async def test_global_observers_are_called_before_scoped_ones(execenv):
    # when
    log = await global_and_scoped_order()
    # then
    assert log == [
        "global:query_started",
        "scoped:query_started",
        "global:query_finished",
        "scoped:query_finished",
    ]


async def test_concurrent_statements_released_in_reverse_order_finish_with_their_own_rowcount(
    execenv,
):
    # when
    recorder = await reversed_release(execenv)
    # then
    assert recorder.finished_pairs == [("SELECT 'second'", 2), ("SELECT 'first'", 1)]


async def test_an_observer_added_twice_is_called_twice_until_removed_twice(execenv):
    # when
    counts = await same_observer_added_twice()
    # then
    assert counts == [4, 6, 6]


async def test_remove_observer_removes_the_most_recent_registration(execenv, settings):
    # given
    settings.DEBUG = False
    # when
    params_seen = await remove_keeps_the_older_registration()
    # then
    assert params_seen == [(1,), (1,)]


# --------------------------------------------------------------------------- failing observers
@pytest.mark.parametrize(("op", "expected"), [("fetch_all", [(1,)]), ("stream", [[(1,)]])])
@FAILING_ON
@SCOPES
@PINNED
async def test_a_failing_observer_does_not_change_the_result(
    execenv, op, expected, on, scope, pinned
):
    # when
    outcome = await with_failing_observer(Failing(on), op, scope=scope, pinned=pinned)
    # then
    assert (outcome.result, outcome.error) == (expected, None)


@FAILING_ON
@SCOPES
@PINNED
async def test_a_failing_observer_does_not_keep_the_event_from_the_others(
    execenv, on, scope, pinned
):
    # when
    outcome = await with_failing_observer(Failing(on), "fetch_all", scope=scope, pinned=pinned)
    # then
    assert outcome.other.names == BOTH


@FAILING_ON
@SCOPES
async def test_a_failing_observer_is_still_given_both_events(execenv, on, scope):
    # when
    outcome = await with_failing_observer(Failing(on), "fetch_all", scope=scope, pinned=False)
    # then
    assert outcome.failing_calls == 2


@FAILING_ON
@SCOPES
async def test_a_failing_observer_is_logged_on_the_connection_logger(execenv, caplog, on, scope):
    # given
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    # when
    await with_failing_observer(Failing(on), "fetch_all", scope=scope, pinned=False)
    # then
    assert logged_failures(caplog, LOGGER) == 1


@FAILING_ON
@SCOPES
async def test_a_failing_observer_log_record_names_the_event(execenv, caplog, on, scope):
    # given
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    # when
    await with_failing_observer(Failing(on), "fetch_all", scope=scope, pinned=False)
    # then
    messages = logged_failure_messages(caplog, LOGGER)
    assert on in messages[0]


BASE_EXCEPTIONS = pytest.mark.parametrize(
    "error_type",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
    ],
)
OPS = pytest.mark.parametrize("op", ["fetch_all", "stream"])


@BASE_EXCEPTIONS
@OPS
@FAILING_ON
@SCOPES
@PINNED
async def test_an_observer_raising_a_base_exception_propagates_it(
    execenv, error_type, op, on, scope, pinned
):
    # given
    error = error_type()
    # when
    observed = await observer_raising(Raising(on, error), op, pinned=pinned, scope=scope)
    # then
    assert observed.error is error


@BASE_EXCEPTIONS
@OPS
@FAILING_ON
@SCOPES
@PINNED
async def test_an_observer_raising_a_base_exception_still_lets_the_next_observer_see_both_events(
    execenv, error_type, op, on, scope, pinned
):
    # when
    observed = await observer_raising(Raising(on, error_type()), op, pinned=pinned, scope=scope)
    # then
    assert observed.names == BOTH


@BASE_EXCEPTIONS
@OPS
@PINNED
async def test_a_base_exception_from_a_started_observer_keeps_the_call_from_the_driver(
    execenv, error_type, op, pinned
):
    # when
    aborted = await aborted_by_started_observer(
        execenv, Raising(STARTED, error_type()), op, pinned=pinned
    )
    # then
    assert (aborted.statements, aborted.gate_calls) == ([], 0)


@BASE_EXCEPTIONS
@OPS
@PINNED
async def test_a_base_exception_from_a_started_observer_is_the_finished_error(
    execenv, error_type, op, pinned
):
    # given
    error = error_type()
    # when
    aborted = await aborted_by_started_observer(execenv, Raising(STARTED, error), op, pinned=pinned)
    # then
    finished = aborted.observed.finished
    assert (finished.error, finished.rowcount) == (error, None)
    assert aborted.observed.error is error


AWAITABLE_OBSERVERS = pytest.mark.parametrize(
    "observer_type",
    [pytest.param(ReturnsCoroutine, id="coroutine"), pytest.param(ReturnsFuture, id="future")],
)


@AWAITABLE_OBSERVERS
async def test_an_observer_returning_an_awaitable_does_not_change_the_result(
    execenv, observer_type
):
    # when
    outcome = await observer_returning_an_awaitable(observer_type())
    # then
    assert (outcome.result, outcome.error, outcome.names) == ([(1,)], None, BOTH)


async def test_an_observer_returning_a_coroutine_leaves_no_never_awaited_warning(execenv):
    # when
    outcome = await observer_returning_an_awaitable(ReturnsCoroutine())
    # then
    assert outcome.never_awaited == []


@AWAITABLE_OBSERVERS
async def test_an_observer_returning_an_awaitable_is_logged_as_an_error_per_event(
    execenv, caplog, observer_type
):
    # given
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    # when
    await observer_returning_an_awaitable(observer_type())
    # then
    messages = error_messages(caplog, LOGGER)
    assert len(messages) == 2
    assert (STARTED in messages[0], FINISHED in messages[1]) == (True, True)


async def test_remove_observer_of_a_bound_method_unregisters_it(execenv):
    # when
    before, after = await bound_method_registration()
    # then
    assert (before, after) == (["SELECT 'registered'"], ["SELECT 'registered'"])
