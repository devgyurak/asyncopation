"""Pinned transactions under observers (issue #4, ADR-0005 with ADR-0001 6c): only what the driver
itself raised may break a transaction, a control call an observer stopped before the driver
changes no transaction state, and an observer re-raising the call's own error adds no note."""

import asyncio

import pytest
from django.db.transaction import TransactionManagementError

from tests.support.executors.fakes import LostConnection
from tests.support.executors.ops import CONTROLS
from tests.support.hooks.aborts import notes_of
from tests.support.hooks.events import BOTH, FINISHED, STARTED, Failing, Recorder
from tests.support.hooks.transactions import (
    EVERY_CALL,
    Reraiser,
    another_observer_note,
    cancel_after_observed_control,
    cancel_observed_transaction_call,
    cancellation_from_another_call_escapes_a_stream,
    compare_with_failing_observer,
    control_aborted_at_start,
    lost_connection_in_observed_transaction,
    lost_connection_on_observed_stream_close,
    reraise_driver_error,
    reraise_real_cancellation,
    rollback_after_aborted_commit,
    two_observers_raising,
    two_reraisers_on_a_failed_call,
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "stream"])
ON = pytest.mark.parametrize("on", [STARTED, FINISHED])
REAL_CANCEL_POINTS = pytest.mark.parametrize(
    "where", ["fetch_all", "stream-open", "stream-read", "stream-close"]
)
OBSERVERS = pytest.mark.parametrize(
    "observer_type",
    [pytest.param(Recorder, id="recorder"), pytest.param(Reraiser, id="reraiser")],
)
BASE_EXCEPTIONS = pytest.mark.parametrize(
    "error_type",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
    ],
)


# --------------------------------------------------------------------------- re-raising observers
@REAL_CANCEL_POINTS
async def test_an_observer_reraising_a_real_cancellation_still_breaks_the_transaction(
    execenv, where
):
    # when
    outcome = await reraise_real_cancellation(execenv, where)
    # then
    assert (outcome.cancelled, outcome.broken) == (True, True)


@REAL_CANCEL_POINTS
async def test_an_observer_reraising_a_real_cancellation_adds_no_note(execenv, where):
    # when
    outcome = await reraise_real_cancellation(execenv, where)
    # then
    assert outcome.notes == []


@OPS
@PINNED
async def test_an_observer_reraising_the_driver_error_adds_no_note(execenv, op, pinned):
    # when
    observed = await reraise_driver_error(execenv, op, pinned=pinned)
    # then
    assert observed.error is observed.injected
    assert notes_of(observed.error) == []


@OBSERVERS
async def test_a_real_cancellation_of_an_observed_pinned_stream_open_breaks_the_transaction(
    execenv, observer_type
):
    # when
    outcome = await cancel_observed_transaction_call(execenv, "stream-open", observer_type())
    # then
    assert (outcome.cancelled, outcome.broken) == (True, True)


@OBSERVERS
async def test_a_real_cancellation_of_an_observed_pinned_stream_close_breaks_the_transaction(
    execenv, observer_type
):
    # when
    outcome = await cancel_observed_transaction_call(execenv, "stream-close", observer_type())
    # then
    assert (outcome.cancelled, outcome.broken) == (True, True)


async def test_a_lost_connection_on_an_observed_pinned_stream_close_breaks_the_transaction(
    execenv,
):
    # when
    error, broken = await lost_connection_on_observed_stream_close(execenv)
    # then
    assert isinstance(error, LostConnection)
    assert broken is True


async def test_a_lost_connection_in_an_observed_transaction_breaks_the_pin(execenv):
    # when
    broken, next_error = await lost_connection_in_observed_transaction(execenv)
    # then
    assert broken is True
    assert isinstance(next_error, TransactionManagementError)


@OPS
@PINNED
async def test_two_observers_reraising_the_driver_error_add_no_note(execenv, op, pinned):
    # when
    observed = await two_reraisers_on_a_failed_call(execenv, op, pinned=pinned)
    # then
    assert observed.error is observed.injected
    assert notes_of(observed.error) == []


async def test_a_cancellation_from_another_call_escaping_an_unobserved_stream_breaks_the_pin(
    execenv,
):
    # when
    error, broken = await cancellation_from_another_call_escapes_a_stream(execenv)
    # then
    assert isinstance(error, asyncio.CancelledError)
    assert broken is True


# --------------------------------------------------------------------------- control calls
@BASE_EXCEPTIONS
@pytest.mark.parametrize("op", CONTROLS)
async def test_a_base_exception_from_a_started_observer_keeps_a_control_call_from_the_driver(
    execenv, error_type, op
):
    # when
    aborted = await control_aborted_at_start(execenv, op, error_type())
    # then
    assert aborted.error is aborted.injected
    assert aborted.kinds == []


@BASE_EXCEPTIONS
@pytest.mark.parametrize("op", CONTROLS)
async def test_a_control_call_stopped_by_a_started_observer_finishes_with_its_error(
    execenv, error_type, op
):
    # when
    aborted = await control_aborted_at_start(execenv, op, error_type())
    # then
    assert aborted.recorder.names == BOTH
    assert (aborted.recorder.only_finished.error, aborted.recorder.only_finished.rowcount) == (
        aborted.injected,
        None,
    )


@pytest.mark.parametrize(
    ("control", "on", "begin_first", "kinds", "broken"),
    [
        pytest.param("begin", STARTED, False, [], False, id="begin-stopped-before-the-driver"),
        pytest.param("begin", FINISHED, False, ["begin"], True, id="begin-reached-the-driver"),
        pytest.param(
            "commit", STARTED, True, ["begin"], True, id="commit-stopped-before-the-driver"
        ),
        pytest.param(
            "commit", FINISHED, True, ["begin", "commit"], False, id="commit-reached-the-driver"
        ),
        pytest.param(
            "rollback", STARTED, True, ["begin"], True, id="rollback-stopped-before-the-driver"
        ),
        pytest.param(
            "rollback",
            FINISHED,
            True,
            ["begin", "rollback"],
            False,
            id="rollback-reached-the-driver",
        ),
    ],
)
async def test_a_real_cancellation_after_an_observer_stopped_control_breaks_only_an_open_transaction(
    execenv, control, on, begin_first, kinds, broken
):
    # when
    after = await cancel_after_observed_control(execenv, control, on, begin_first=begin_first)
    # then
    assert isinstance(after.control_error, asyncio.CancelledError)
    assert (after.kinds, after.broken) == (kinds, broken)


async def test_rollback_after_a_commit_stopped_by_an_observer_sends_rollback(execenv):
    # when
    error, kinds = await rollback_after_aborted_commit(execenv)
    # then
    assert (error, kinds) == (None, ["begin", "rollback"])


# --------------------------------------------------------------------------- several observers
@ON
@OPS
@PINNED
async def test_the_first_of_two_raising_observers_propagates_with_a_note_naming_the_second(
    execenv, on, op, pinned
):
    # given
    first, second = KeyboardInterrupt("first"), SystemExit("second")
    # when
    error = await two_observers_raising(on, first, second, op, pinned=pinned)
    # then
    assert error is first
    assert notes_of(error) == [another_observer_note(on, second)]


@ON
@pytest.mark.parametrize(("op", "pinned"), EVERY_CALL)
async def test_a_failing_observer_keeps_the_result_and_rowcount_of_every_call(
    execenv, on, op, pinned
):
    # when
    compared = await compare_with_failing_observer(Failing(on), op, pinned=pinned)
    # then
    assert compared.error is None
    assert compared.with_failing == compared.plain
