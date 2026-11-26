"""Pinned transactions under observers (issue #4, ADR-0005 with ADR-0001 6c): only what the driver
itself raised may break a transaction, a control call an observer stopped before the driver
changes no transaction state, and an observer re-raising the call's own error adds no note."""

import asyncio

import pytest

from tests.support.executors.ops import CONTROLS
from tests.support.hooks.aborts import notes_of
from tests.support.hooks.events import BOTH, FINISHED, STARTED, Failing
from tests.support.hooks.transactions import (
    EVERY_CALL,
    another_observer_note,
    cancel_after_observed_control,
    cancellation_from_another_call_escapes_a_stream,
    compare_with_failing_observer,
    control_aborted_at_start,
    reraise_driver_error,
    reraise_real_cancellation,
    rollback_after_aborted_commit,
    two_observers_raising,
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "stream"])
ON = pytest.mark.parametrize("on", [STARTED, FINISHED])
BASE_EXCEPTIONS = pytest.mark.parametrize(
    "error_type",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
    ],
)


# --------------------------------------------------------------------------- re-raising observers
@OPS
async def test_an_observer_reraising_a_real_cancellation_still_breaks_the_transaction(execenv, op):
    # when
    outcome = await reraise_real_cancellation(execenv, op)
    # then
    assert (outcome.cancelled, outcome.broken) == (True, True)


@OPS
async def test_an_observer_reraising_a_real_cancellation_adds_no_note(execenv, op):
    # when
    outcome = await reraise_real_cancellation(execenv, op)
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
