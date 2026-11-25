"""Streams whose close fails (rollback, pin exit) and streams that are entered more than once."""

import asyncio

import pytest

from tests.support.executors.fakes import LostConnection
from tests.support.executors.ownership import broken_after_a_stream_body_raises
from tests.support.executors.stream_exit import (
    close_a_stream_that_was_never_entered,
    close_error_at_pin_exit,
    enter_stream_after_close,
    enter_stream_twice,
    enter_stream_whose_open_fails,
    iterate_a_stream_that_was_never_entered,
    pin_exit_with_failing_stream_close,
    reenter_a_stream_whose_open_failed,
    rollback_when_stream_close_fails,
    rollback_when_the_stream_close_is_cancelled,
)

ENTERED_ONCE = "a stream can be entered only once"
REENTRIES = [enter_stream_twice, enter_stream_after_close]
REENTRY_IDS = ["twice", "after_close"]


async def test_rollback_still_sends_the_rollback_when_a_stream_close_fails(execenv):
    # when
    outcome = await rollback_when_stream_close_fails(execenv)
    # then
    assert outcome.kinds == ["begin", "stream", "rollback"]


async def test_rollback_raises_the_stream_close_error_after_rolling_back(execenv):
    # when
    outcome = await rollback_when_stream_close_fails(execenv)
    # then
    assert (type(outcome.error), str(outcome.error)) == (ValueError, "close failed")


@pytest.mark.parametrize(
    "faults",
    [[ValueError, asyncio.CancelledError], [asyncio.CancelledError, ValueError]],
    ids=["ordinary-first", "cancellation-first"],
)
async def test_a_cancellation_during_stream_closes_wins_over_an_ordinary_error(execenv, faults):
    # when
    error = await close_error_at_pin_exit(execenv, faults)
    # then
    assert type(error) is asyncio.CancelledError


async def test_the_block_error_wins_over_a_failing_stream_close_at_pin_exit(execenv):
    # when
    outcome = await pin_exit_with_failing_stream_close(execenv, body_raises=True)
    # then
    assert (type(outcome.error), str(outcome.error)) == (ValueError, "boom")


async def test_the_pin_is_torn_down_when_the_block_and_a_stream_close_both_fail(execenv):
    # when
    outcome = await pin_exit_with_failing_stream_close(execenv, body_raises=True)
    # then
    assert (outcome.executor_closed, outcome.in_flight, outcome.released) == (True, 0, [True])


async def test_a_failing_stream_close_is_raised_when_the_block_ends_cleanly(execenv):
    # when
    outcome = await pin_exit_with_failing_stream_close(execenv, body_raises=False)
    # then
    assert (type(outcome.error), str(outcome.error)) == (RuntimeError, "close failed")


async def test_the_pin_is_torn_down_when_only_a_stream_close_fails(execenv):
    # when
    outcome = await pin_exit_with_failing_stream_close(execenv, body_raises=False)
    # then
    assert (outcome.executor_closed, outcome.in_flight, outcome.released) == (True, 0, [True])


@pytest.mark.parametrize("scenario", REENTRIES, ids=REENTRY_IDS)
async def test_a_stream_cannot_be_entered_a_second_time(execenv, scenario):
    # when
    outcome = await scenario(execenv)
    # then
    assert (type(outcome.error), str(outcome.error)) == (RuntimeError, ENTERED_ONCE)


@pytest.mark.parametrize("scenario", REENTRIES, ids=REENTRY_IDS)
async def test_a_refused_second_entry_leaves_the_pin_usable(execenv, scenario):
    # when
    outcome = await scenario(execenv)
    # then
    assert outcome.rows_after == [(1,)]


async def test_a_stream_whose_open_fails_raises_the_driver_error(execenv):
    # when
    outcome = await enter_stream_whose_open_fails(execenv)
    # then
    assert (type(outcome.error), str(outcome.error)) == (ValueError, "open failed")


async def test_a_stream_whose_open_fails_leaves_the_pin_usable(execenv):
    # when
    outcome = await enter_stream_whose_open_fails(execenv)
    # then
    assert outcome.rows_after == [(1,)]


async def test_rollback_raises_the_cancellation_that_broke_the_transaction_while_closing_a_stream(
    execenv,
):
    # when
    outcome = await rollback_when_the_stream_close_is_cancelled(execenv)
    # then
    assert type(outcome.error) is asyncio.CancelledError


async def test_rollback_sends_no_rollback_after_a_stream_close_broke_the_transaction(execenv):
    # when
    outcome = await rollback_when_the_stream_close_is_cancelled(execenv)
    # then
    assert outcome.kinds == ["begin", "stream"]


async def test_a_stream_whose_open_failed_cannot_be_entered_again(execenv):
    # when
    outcome = await reenter_a_stream_whose_open_failed(execenv)
    # then
    assert (type(outcome.error), str(outcome.error)) == (RuntimeError, ENTERED_ONCE)


async def test_a_refused_second_entry_after_a_failed_open_leaves_the_pin_usable(execenv):
    # when
    outcome = await reenter_a_stream_whose_open_failed(execenv)
    # then
    assert outcome.rows_after == [(1,)]


async def test_closing_a_stream_that_was_never_entered_is_a_no_op(execenv):
    # when
    outcome = await close_a_stream_that_was_never_entered(execenv)
    # then
    assert outcome.close_error is None


async def test_the_pin_stays_usable_after_closing_a_stream_that_was_never_entered(execenv):
    # when
    outcome = await close_a_stream_that_was_never_entered(execenv)
    # then
    assert outcome.rows_after == [(1,)]


async def test_a_stream_closed_before_it_was_entered_cannot_be_entered(execenv):
    # when
    outcome = await close_a_stream_that_was_never_entered(execenv)
    # then
    assert (type(outcome.enter_error), str(outcome.enter_error)) == (RuntimeError, ENTERED_ONCE)


async def test_iterating_a_stream_that_was_never_entered_raises_runtime_error(execenv):
    # when
    error = await iterate_a_stream_that_was_never_entered(execenv)
    # then
    assert type(error) is RuntimeError
    assert "async with" in str(error)


@pytest.mark.parametrize(
    ("error_type", "broken"),
    [
        pytest.param(ValueError, False, id="ordinary-error"),
        pytest.param(LostConnection, False, id="lost-connection-in-the-body"),
        pytest.param(asyncio.CancelledError, True, id="cancellation"),
    ],
)
async def test_only_a_cancelled_body_breaks_the_transaction_when_its_stream_closes(
    execenv, error_type, broken
):
    # when
    outcome = await broken_after_a_stream_body_raises(execenv, error_type)
    # then
    assert outcome is broken
