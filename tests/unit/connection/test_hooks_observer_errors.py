"""Exceptions raised by observers (ADR-0005, issue #4): one raised by an observer never breaks a
pinned transaction (the connection never saw it), and on a failure path a `query_finished`
observer that raises never replaces the original exception - it is attached as a note."""

import asyncio

import pytest

from tests.support.hooks.aborts import (
    RaisingEach,
    consumer_error_with_raising_observer,
    failed_call_with_raising_observer,
    notes_of,
    observer_note,
    observer_raising_in_transaction,
    raising_on_both_events,
    real_cancel_in_observed_transaction,
)
from tests.support.hooks.events import FINISHED, STARTED, Raising
from tests.support.hooks.lifetime import observer_raising
from tests.support.hooks.streams import ConsumerError

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "stream"])
ON = pytest.mark.parametrize("on", [STARTED, FINISHED])
LATE_ERRORS = pytest.mark.parametrize(
    "late_type",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
        pytest.param(SystemExit, id="system-exit"),
    ],
)


# --------------------------------------------------------------------------- pinned transactions
@OPS
@ON
async def test_a_cancellation_raised_by_an_observer_propagates_out_of_a_transaction_statement(
    execenv, op, on
):
    # given
    error = asyncio.CancelledError()
    # when
    outcome = await observer_raising_in_transaction(execenv, Raising(on, error), op)
    # then
    assert outcome.error is error


@OPS
@ON
async def test_a_cancellation_raised_by_an_observer_does_not_break_the_transaction(execenv, op, on):
    # when
    outcome = await observer_raising_in_transaction(
        execenv, Raising(on, asyncio.CancelledError()), op
    )
    # then
    assert outcome.broken is False


@OPS
@ON
async def test_after_a_cancellation_raised_by_an_observer_rollback_sends_rollback(execenv, op, on):
    # when
    outcome = await observer_raising_in_transaction(
        execenv, Raising(on, asyncio.CancelledError()), op
    )
    # then
    assert outcome.rollback_error is None
    assert outcome.kinds[-1] == "rollback"


async def test_a_real_cancellation_in_an_observed_transaction_still_breaks_it(execenv):
    # contrast: tests/unit/connection/test_executor_broken.py covers the unobserved case
    # when
    cancelled, broken = await real_cancel_in_observed_transaction(execenv)
    # then
    assert (cancelled, broken) == (True, True)


# --------------------------------------------------------------------------- the original wins
@LATE_ERRORS
@OPS
@PINNED
async def test_a_driver_error_wins_over_a_finished_observer_that_raises(
    execenv, late_type, op, pinned
):
    # given
    late = late_type()
    # when
    observed = await failed_call_with_raising_observer(execenv, op, late, pinned=pinned)
    # then
    assert observed.error is observed.injected
    assert notes_of(observed.error) == [observer_note(late)]


@LATE_ERRORS
@OPS
@PINNED
async def test_the_other_observers_still_see_a_failed_call_whose_finished_observer_raises(
    execenv, late_type, op, pinned
):
    # when
    observed = await failed_call_with_raising_observer(execenv, op, late_type(), pinned=pinned)
    # then
    assert observed.finished.error is observed.injected


@OPS
@PINNED
async def test_a_started_observer_error_wins_over_its_finished_error(execenv, op, pinned):
    # given
    started, finished = KeyboardInterrupt("started"), SystemExit("finished")
    # when
    error = await raising_on_both_events(RaisingEach(started, finished), op, pinned=pinned)
    # then
    assert error is started
    assert notes_of(error) == [observer_note(finished)]


@LATE_ERRORS
@PINNED
async def test_a_consumer_error_wins_over_a_finished_observer_that_raises(
    execenv, late_type, pinned
):
    # given
    late = late_type()
    # when
    error = await consumer_error_with_raising_observer(execenv, late, pinned=pinned)
    # then
    assert isinstance(error, ConsumerError)
    assert notes_of(error) == [observer_note(late)]


@LATE_ERRORS
@OPS
@PINNED
async def test_a_finished_observer_error_after_a_successful_call_propagates_without_a_note(
    execenv, late_type, op, pinned
):
    # given
    late = late_type()
    # when
    observed = await observer_raising(Raising(FINISHED, late), op, pinned=pinned)
    # then
    assert observed.error is late
    assert notes_of(late) == []
