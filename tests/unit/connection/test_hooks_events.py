"""ADR-0005 observer events fired by the executors (issue #4): when they fire, their kind, SQL,
rowcount, errors, parameters and timings, on the gated recording drivers."""

import asyncio

import pytest

from asyncopation.drivers.base import ExecResult
from tests.support.executors.ops import CONTROLS, STATEMENTS
from tests.support.hooks.events import BOTH, STARTED
from tests.support.hooks.lifetime import (
    cancel_held_control,
    failed_control,
    observed_on_alias,
    run_with_observed_call_refused,
)
from tests.support.hooks.scenarios import (
    cancel_held_statement,
    global_with_params,
    names_while_borrow_is_held,
    names_while_statement_is_held,
    observed_call,
    observed_call_with,
    observed_failure,
    opted_in_and_out,
    run_without_subscribers,
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
EXECUTES = pytest.mark.parametrize("op", ["execute", "execute_many"])


# --------------------------------------------------------------------------- when events fire
@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_statement_is_observed_as_started_then_finished(execenv, op, pinned):
    # when
    observed = await observed_call(op, pinned=pinned)
    # then
    assert observed.names == BOTH


@pytest.mark.parametrize("op", CONTROLS)
async def test_a_transaction_control_call_is_observed_as_started_then_finished(execenv, op):
    # when
    observed = await observed_call(op, pinned=True)
    # then
    assert observed.names == BOTH


async def test_no_event_fires_while_a_borrowed_statement_waits_for_a_connection(execenv):
    # when
    moments = await names_while_borrow_is_held(execenv)
    # then
    assert moments.while_waiting == []
    assert moments.after == BOTH


@PINNED
async def test_only_the_started_event_has_fired_while_the_driver_runs_the_statement(
    execenv, pinned
):
    # when
    moments = await names_while_statement_is_held(execenv, pinned=pinned)
    # then
    assert moments.while_waiting == [STARTED]
    assert moments.after == BOTH


@pytest.mark.parametrize("op", STATEMENTS)
async def test_a_borrow_that_fails_in_the_pool_emits_no_event(execenv, op):
    # given
    execenv.default.borrow_error = TimeoutError("pool timeout")
    # when
    observed = await observed_call(op, pinned=False)
    # then
    assert isinstance(observed.error, TimeoutError)
    assert observed.names == []


# --------------------------------------------------------------------------- kind, sql, params, many
@pytest.mark.parametrize(
    ("op", "sql", "params", "many"),
    [
        ("fetch_all", "SELECT 1", None, False),
        ("execute", "UPDATE t SET x = 1", None, False),
        ("execute_many", "INSERT INTO t VALUES (%s)", [(1,), (2,)], True),
        ("stream", "SELECT 1", None, False),
    ],
)
@PINNED
async def test_a_statement_event_carries_kind_query_its_sql_params_and_many(
    execenv, op, sql, params, many, pinned
):
    # when
    observed = await observed_call(op, pinned=pinned, params=True)
    # then
    assert observed.recorder.shapes == [
        ("query_started", "default", "query", sql, params, many),
        ("query_finished", "default", "query", sql, params, many),
    ]


@pytest.mark.parametrize(
    ("op", "kind", "sql"),
    [
        ("begin", "transaction", "BEGIN"),
        ("commit", "transaction", "COMMIT"),
        ("rollback", "transaction", "ROLLBACK"),
        ("savepoint", "query", "SAVEPOINT sp"),
        ("release_savepoint", "query", "RELEASE SAVEPOINT sp"),
        ("rollback_to_savepoint", "query", "ROLLBACK TO SAVEPOINT sp"),
    ],
)
async def test_a_control_event_carries_its_kind_and_descriptive_sql(execenv, op, kind, sql):
    # when
    observed = await observed_call(op, pinned=True, params=True)
    # then
    assert observed.recorder.shapes == [
        ("query_started", "default", kind, sql, None, False),
        ("query_finished", "default", kind, sql, None, False),
    ]


# --------------------------------------------------------------------------- rowcount
@PINNED
async def test_fetch_all_finished_rowcount_is_the_number_of_rows(execenv, pinned):
    # given
    execenv.default.queue([(1,), (2,), (3,)])
    # when
    observed = await observed_call("fetch_all", pinned=pinned)
    # then
    assert observed.finished.rowcount == 3


@pytest.mark.parametrize(("op", "expected"), [("fetch_all", 0), ("stream", 0)])
@PINNED
async def test_an_empty_result_finishes_with_rowcount_zero(execenv, op, expected, pinned):
    # given
    execenv.default.queue([])
    # when
    observed = await observed_call(op, pinned=pinned)
    # then
    assert observed.finished.rowcount == expected


@pytest.mark.parametrize("rowcount", [7, 0, -1])
@EXECUTES
@PINNED
async def test_execute_finished_rowcount_is_the_driver_rowcount(execenv, rowcount, op, pinned):
    # given
    execenv.default.queue(ExecResult(rowcount=rowcount))
    # when
    observed = await observed_call(op, pinned=pinned)
    # then
    assert observed.finished.rowcount == rowcount


@pytest.mark.parametrize("op", CONTROLS)
async def test_a_control_event_has_no_rowcount(execenv, op):
    # when
    observed = await observed_call(op, pinned=True)
    # then
    assert (observed.started.rowcount, observed.finished.rowcount) == (None, None)


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_started_event_has_no_rowcount_error_query_time_or_total_time(execenv, op, pinned):
    # when
    observed = await observed_call(op, pinned=pinned)
    # then
    started = observed.started
    assert (started.rowcount, started.error, started.query_time, started.total_time) == (
        None,
        None,
        None,
        None,
    )


# --------------------------------------------------------------------------- errors
@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_driver_error_propagates_unchanged(execenv, op, pinned):
    # when
    observed = await observed_failure(execenv, op, pinned=pinned)
    # then
    assert observed.error is observed.injected


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_driver_error_is_the_finished_event_error(execenv, op, pinned):
    # when
    observed = await observed_failure(execenv, op, pinned=pinned)
    # then
    assert observed.names == BOTH
    assert observed.finished.error is observed.injected


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_failed_statement_has_no_rowcount(execenv, op, pinned):
    # when
    observed = await observed_failure(execenv, op, pinned=pinned)
    # then
    assert observed.finished.rowcount is None


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_successful_statement_has_no_finished_error(execenv, op, pinned):
    # when
    observed = await observed_call(op, pinned=pinned)
    # then
    assert observed.finished.error is None


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_a_statement_cancelled_in_the_driver_finishes_with_a_cancelled_error(
    execenv, op, pinned
):
    # when
    observed = await cancel_held_statement(execenv, op, pinned=pinned)
    # then
    assert observed.cancelled is True
    assert observed.names == BOTH
    assert isinstance(observed.finished.error, asyncio.CancelledError)


@pytest.mark.parametrize("op", CONTROLS)
async def test_a_control_call_cancelled_in_the_driver_finishes_with_a_cancelled_error(execenv, op):
    # when
    observed = await cancel_held_control(execenv, op)
    # then
    assert observed.cancelled is True
    assert observed.names == BOTH
    assert isinstance(observed.finished.error, asyncio.CancelledError)


async def test_a_failed_transaction_control_call_is_the_finished_error(execenv):
    # when
    observed = await failed_control(execenv)
    # then
    assert observed.error is observed.injected
    assert (observed.finished.error, observed.finished.rowcount) == (observed.injected, None)


# --------------------------------------------------------------------------- alias
@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_an_event_carries_the_alias_of_its_executor(execenv, op, pinned):
    # when
    recorder = await observed_on_alias("other", op, pinned=pinned)
    # then
    assert recorder.aliases == ["other", "other"]


# --------------------------------------------------------------------------- params gating
@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_params_are_hidden_without_opt_in_and_without_debug(execenv, settings, op, pinned):
    # given
    settings.DEBUG = False
    # when
    observed = await observed_call_with(op, "SELECT %s", (1,), pinned=pinned)
    # then
    assert observed.recorder.params_seen == [None, None]


@pytest.mark.parametrize("op", ["fetch_all", "execute", "stream"])
@PINNED
async def test_params_are_given_to_an_observer_that_opted_in(execenv, settings, op, pinned):
    # given
    settings.DEBUG = False
    # when
    observed = await observed_call_with(op, "SELECT %s", (1,), pinned=pinned, params=True)
    # then
    assert observed.recorder.params_seen == [(1,), (1,)]


@PINNED
async def test_execute_many_params_are_the_batch_for_an_observer_that_opted_in(
    execenv, settings, pinned
):
    # given
    settings.DEBUG = False
    # when
    observed = await observed_call_with(
        "execute_many", "INSERT INTO t VALUES (%s)", [(1,), (2,)], pinned=pinned, params=True
    )
    # then
    assert observed.recorder.params_seen == [[(1,), (2,)], [(1,), (2,)]]
    assert observed.finished.many is True


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_params_are_given_to_every_observer_when_debug_is_true(execenv, settings, op, pinned):
    # given
    settings.DEBUG = True
    # when
    observed = await observed_call_with(op, "SELECT %s", (1,), pinned=pinned)
    # then
    assert observed.recorder.params_seen == [(1,), (1,)]


async def test_only_the_observer_that_opted_in_sees_the_params(execenv, settings):
    # given
    settings.DEBUG = False
    # when
    two = await opted_in_and_out()
    # then
    assert two.opted_in.params_seen == [(1,), (1,)]
    assert two.opted_out.params_seen == [None, None]


async def test_a_global_observer_that_opted_in_sees_the_params(execenv, settings):
    # given
    settings.DEBUG = False
    # when
    recorder = await global_with_params()
    # then
    assert recorder.params_seen == [(1,), (1,)]


# --------------------------------------------------------------------------- zero subscribers
@pytest.mark.parametrize(
    "kind",
    [
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
    ],
)
def test_run_without_subscribers_returns_what_the_connection_returned(kind):
    # when
    passthrough = run_without_subscribers(kind)
    # then
    assert passthrough.same_object is True


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_pinned_call_without_observers_never_takes_the_observed_path(
    execenv, monkeypatch, op
):
    # when
    error = await run_with_observed_call_refused(monkeypatch, op, pinned=True)
    # then
    assert error is None


@pytest.mark.parametrize("op", STATEMENTS)
async def test_a_borrowed_statement_without_observers_never_takes_the_observed_path(
    execenv, monkeypatch, op
):
    # when
    error = await run_with_observed_call_refused(monkeypatch, op, pinned=False)
    # then
    assert error is None
