"""Pinned transactions under interceptors (issue #4, ADR-0005 with ADR-0001 6c): an interceptor's own
exception or cancellation never breaks the transaction; what the driver itself raised does, even
when an interceptor swallows it, and even with no observer registered."""

import asyncio

import pytest
from django.db import IntegrityError, InterfaceError
from django.db.transaction import TransactionManagementError

from tests.support.executors.fakes import LostConnection
from tests.support.hooks.intercepting import (
    HELD_IN_TRANSACTION,
    cancel_held_call_swallowed_by_interceptor,
    cancel_held_call_under_interceptor,
    cancel_waiting_interceptor_in_transaction,
    intercepted_in_transaction,
    intercepted_stream_and_controls,
    only_intercepted_call,
)
from tests.support.hooks.interceptors import (
    Catching,
    CatchingTwice,
    PassThrough,
    RaisingAfter,
    RaisingBefore,
    Retrying,
    driver_result,
    failing_once,
    supplied_result,
)
from tests.support.hooks.streams import ROWS

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "execute", "execute_many"])
RAISED = pytest.mark.parametrize(
    "error_type",
    [
        pytest.param(ValueError, id="exception"),
        pytest.param(asyncio.CancelledError, id="cancelled"),
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
    ],
)
WAITING = pytest.mark.parametrize("after", [False, True], ids=["before-execute", "after-execute"])
SWALLOWED_BREAKING = pytest.mark.parametrize(
    "error_type",
    [
        pytest.param(asyncio.CancelledError, id="cancelled"),
        pytest.param(LostConnection, id="lost-connection"),
    ],
)


# --------------------------------------------------------------------------- interceptor errors
@RAISED
async def test_an_interceptor_raising_before_execute_propagates_its_error(execenv, error_type):
    # given
    error = error_type()
    # when
    outcome = await intercepted_in_transaction(execenv, RaisingBefore(error))
    # then
    assert outcome.error is error


@RAISED
async def test_an_interceptor_raising_before_execute_does_not_break_the_transaction(
    execenv, error_type
):
    # when
    outcome = await intercepted_in_transaction(execenv, RaisingBefore(error_type()))
    # then
    assert outcome.broken is False


@RAISED
async def test_rollback_after_an_interceptor_raised_before_execute_sends_rollback(
    execenv, error_type
):
    # when
    outcome = await intercepted_in_transaction(execenv, RaisingBefore(error_type()))
    # then
    assert outcome.rollback_error is None
    assert outcome.kinds == ["begin", "rollback"]


@RAISED
async def test_an_interceptor_raising_after_execute_does_not_break_the_transaction(
    execenv, error_type
):
    # given
    error = error_type()
    # when
    outcome = await intercepted_in_transaction(execenv, RaisingAfter(error))
    # then
    assert (outcome.error, outcome.broken, outcome.rollback_error) == (error, False, None)
    assert outcome.kinds == ["begin", "fetch_all", "rollback"]


@WAITING
async def test_a_real_cancellation_inside_an_interceptor_propagates(execenv, after):
    # when
    outcome = await cancel_waiting_interceptor_in_transaction(execenv, after=after)
    # then
    assert isinstance(outcome.error, asyncio.CancelledError)


@WAITING
async def test_a_real_cancellation_inside_an_interceptor_does_not_break_the_transaction(
    execenv, after
):
    # when
    outcome = await cancel_waiting_interceptor_in_transaction(execenv, after=after)
    # then
    assert outcome.broken is False


@pytest.mark.parametrize(
    ("after", "kinds"),
    [
        pytest.param(False, ["begin", "rollback"], id="before-execute"),
        pytest.param(True, ["begin", "fetch_all", "rollback"], id="after-execute"),
    ],
)
async def test_rollback_after_a_real_cancellation_inside_an_interceptor_sends_rollback(
    execenv, after, kinds
):
    # when
    outcome = await cancel_waiting_interceptor_in_transaction(execenv, after=after)
    # then
    assert outcome.rollback_error is None
    assert outcome.kinds == kinds


# --------------------------------------------------------------------------- driver failures
async def test_a_real_cancellation_of_the_driver_call_inside_the_chain_breaks_the_transaction(
    execenv,
):
    # when
    error, broken = await cancel_held_call_under_interceptor(execenv, "fetch_all")
    # then
    assert isinstance(error, asyncio.CancelledError)
    assert broken is True


@SWALLOWED_BREAKING
async def test_an_interceptor_swallowing_a_breaking_driver_error_returns_its_result(
    execenv, error_type
):
    # given
    supplied = supplied_result("fetch_all")
    # when
    outcome = await intercepted_in_transaction(execenv, Catching(supplied), queued=error_type())
    # then
    assert (outcome.result, outcome.error) == (supplied, None)


@SWALLOWED_BREAKING
async def test_an_interceptor_swallowing_a_breaking_driver_error_still_breaks_the_transaction(
    execenv, error_type
):
    # when
    outcome = await intercepted_in_transaction(
        execenv, Catching(supplied_result("fetch_all")), queued=error_type()
    )
    # then
    assert outcome.broken is True


async def test_an_interceptor_swallowing_an_ordinary_driver_error_does_not_break_the_transaction(
    execenv,
):
    # when
    outcome = await intercepted_in_transaction(
        execenv, Catching(supplied_result("fetch_all")), queued=ValueError("driver failure")
    )
    # then
    assert (outcome.error, outcome.broken, outcome.rollback_error) == (None, False, None)


@SWALLOWED_BREAKING
async def test_a_statement_after_a_swallowed_breaking_driver_error_raises(execenv, error_type):
    # when
    outcome = await intercepted_in_transaction(
        execenv, Catching(supplied_result("fetch_all")), queued=error_type(), follow_up=True
    )
    # then
    assert isinstance(outcome.next_error, TransactionManagementError)


@SWALLOWED_BREAKING
async def test_a_successful_retry_after_a_breaking_driver_error_returns_its_result(
    execenv, error_type
):
    # given
    execenv.default.during = failing_once(execenv.default, error_type())
    interceptor = Retrying(catch=BaseException)
    # when
    outcome = await intercepted_in_transaction(execenv, interceptor, queued=[(5,)])
    # then
    assert (outcome.result, outcome.error) == ([(5,)], None)
    assert isinstance(interceptor.first_error, error_type)


@SWALLOWED_BREAKING
async def test_a_successful_retry_after_a_breaking_driver_error_still_breaks_the_transaction(
    execenv, error_type
):
    # given: the earlier attempt's failure stays recorded (the connection saw it)
    execenv.default.during = failing_once(execenv.default, error_type())
    # when
    outcome = await intercepted_in_transaction(
        execenv, Retrying(catch=BaseException), queued=[(5,)], follow_up=True
    )
    # then
    assert outcome.broken is True
    assert isinstance(outcome.next_error, TransactionManagementError)


ORDINARY_RETRY_ERRORS = pytest.mark.parametrize(
    "retry_error_type",
    [
        pytest.param(ValueError, id="ordinary-error"),
        pytest.param(InterfaceError, id="interface-error-not-a-disconnect"),
    ],
)


@ORDINARY_RETRY_ERRORS
async def test_a_breaking_driver_failure_then_a_non_breaking_one_on_retry_still_breaks_the_transaction(
    execenv, retry_error_type
):
    # given: attempt 1 is cancelled (swallowed by the interceptor), the retry fails ordinarily
    execenv.default.during = failing_once(execenv.default, asyncio.CancelledError())
    retry_error = retry_error_type("retry failed")
    # when
    outcome = await intercepted_in_transaction(
        execenv, Retrying(catch=BaseException), queued=retry_error, follow_up=True
    )
    # then
    assert (outcome.error, outcome.broken) == (retry_error, True)
    assert isinstance(outcome.next_error, TransactionManagementError)


async def test_an_ordinary_driver_failure_then_a_swallowed_cancellation_breaks_the_transaction(
    execenv,
):
    # given
    execenv.default.queue(IntegrityError("duplicate key"), asyncio.CancelledError())
    supplied = supplied_result("fetch_all")
    interceptor = CatchingTwice(supplied)
    # when
    outcome = await intercepted_in_transaction(execenv, interceptor, follow_up=True)
    # then
    assert (outcome.result, outcome.error, outcome.broken) == (supplied, None, True)
    assert isinstance(outcome.next_error, TransactionManagementError)


async def test_a_real_cancellation_swallowed_by_an_interceptor_still_breaks_the_transaction(
    execenv,
):
    # given
    supplied = supplied_result("fetch_all")
    # when
    outcome = await cancel_held_call_swallowed_by_interceptor(execenv, Catching(supplied))
    # then
    assert (outcome.result, outcome.error, outcome.broken) == (supplied, None, True)
    assert isinstance(outcome.next_error, TransactionManagementError)


# --------------------------------------------------------------------------- only interceptors
@OPS
@PINNED
async def test_with_only_an_interceptor_registered_a_statement_returns_the_driver_result(
    execenv, op, pinned
):
    # given
    queued = driver_result(op)
    execenv.default.queue(queued)
    # when
    observed = await only_intercepted_call(op, PassThrough(), pinned=pinned)
    # then
    assert observed.result is queued


@SWALLOWED_BREAKING
async def test_with_only_an_interceptor_registered_a_breaking_driver_error_breaks_the_transaction(
    execenv, error_type
):
    # given
    error = error_type()
    # when
    outcome = await intercepted_in_transaction(execenv, PassThrough(), queued=error)
    # then
    assert (outcome.error, outcome.broken) == (error, True)


@SWALLOWED_BREAKING
async def test_with_only_an_interceptor_registered_rollback_of_a_broken_transaction_is_a_no_op(
    execenv, error_type
):
    # when
    outcome = await intercepted_in_transaction(execenv, PassThrough(), queued=error_type())
    # then
    assert outcome.rollback_error is None
    assert outcome.kinds == ["begin", "fetch_all"]


async def test_with_only_an_interceptor_registered_an_ordinary_driver_error_does_not_break(
    execenv,
):
    # given
    error = ValueError("driver failure")
    # when
    outcome = await intercepted_in_transaction(execenv, PassThrough(), queued=error)
    # then
    assert (outcome.error, outcome.broken) == (error, False)


async def test_with_only_an_interceptor_registered_streams_and_control_run_normally(execenv):
    # given
    interceptor = PassThrough()
    # when
    rows, kinds = await intercepted_stream_and_controls(execenv, interceptor)
    # then
    assert rows == [ROWS[:10], ROWS[10:20], ROWS[20:]]
    assert kinds == ["begin", "stream", "savepoint", "release_savepoint", "commit"]
    assert interceptor.calls == []


@pytest.mark.parametrize("where", HELD_IN_TRANSACTION)
async def test_with_only_an_interceptor_registered_a_real_cancellation_breaks_the_transaction(
    execenv, where
):
    # when
    error, broken = await cancel_held_call_under_interceptor(execenv, where)
    # then
    assert isinstance(error, asyncio.CancelledError)
    assert broken is True


async def test_a_statement_on_a_transaction_broken_under_an_interceptor_raises(execenv):
    # when
    outcome = await intercepted_in_transaction(
        execenv, PassThrough(), queued=LostConnection(), follow_up=True
    )
    # then
    assert isinstance(outcome.next_error, TransactionManagementError)
