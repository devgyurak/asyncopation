"""A cancelled pinned stream breaks the transaction wherever the cancel hits (ADR-0001 6c)."""

import pytest

from tests.support.executors.failures import stream_fails

WHERE = pytest.mark.parametrize("where", ["chunk", "body", "close"])


@WHERE
async def test_a_stream_cancelled_inside_a_transaction_marks_the_pin_broken(execenv, where):
    # when
    outcome = await stream_fails(execenv, where, begin=True)
    # then
    assert outcome.broken is True


@WHERE
async def test_a_stream_cancelled_without_a_transaction_leaves_the_pin_healthy(execenv, where):
    # when
    outcome = await stream_fails(execenv, where, begin=False)
    # then
    assert outcome.broken is False


@WHERE
async def test_the_cancellation_of_a_stream_still_reaches_the_caller(execenv, where):
    # when
    outcome = await stream_fails(execenv, where, begin=True)
    # then
    assert type(outcome.error).__name__ == "CancelledError"
