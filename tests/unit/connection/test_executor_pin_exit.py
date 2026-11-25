"""How a pin block ends when something goes wrong: nothing leaks and close() never waits for it."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation import ConcurrentQueryError
from tests.support.executors.teardown import (
    depth_after_a_nested_pin_raises,
    exit_pin_with_two_open_streams,
    leave_a_pin_from_another_task,
    pin_block_raises,
    pin_unknown_alias,
    pin_when_borrow_fails,
    stream_of_a_closed_pin,
)


async def test_a_pin_whose_connection_cannot_be_borrowed_raises_that_error(execenv):
    # when
    outcome = await pin_when_borrow_fails(execenv)
    # then
    assert type(outcome.error) is RuntimeError
    assert str(outcome.error) == "pool exhausted"


async def test_a_pin_whose_connection_cannot_be_borrowed_leaves_nothing_in_flight(execenv):
    # when
    outcome = await pin_when_borrow_fails(execenv)
    # then
    assert outcome.in_flight == 0


async def test_close_does_not_wait_for_a_pin_that_could_not_borrow(execenv):
    # when
    outcome = await pin_when_borrow_fails(execenv)
    # then
    assert outcome.close_error is None


async def test_pinning_an_unknown_alias_raises_improperly_configured(execenv):
    # when
    outcome = await pin_unknown_alias()
    # then
    assert type(outcome.error) is ImproperlyConfigured


async def test_pinning_an_unknown_alias_leaves_nothing_in_flight(execenv):
    # when
    outcome = await pin_unknown_alias()
    # then
    assert outcome.in_flight == 0


async def test_an_exception_in_the_block_propagates_unchanged(execenv):
    # when
    outcome = await pin_block_raises(execenv)
    # then
    assert (type(outcome.error), str(outcome.error)) == (ValueError, "boom")


async def test_an_exception_in_the_block_closes_the_executor_and_restores_the_borrowed_one(execenv):
    # when
    outcome = await pin_block_raises(execenv)
    # then
    assert (outcome.executor_closed, outcome.current_is_pinned) == (True, False)


async def test_an_exception_in_the_block_releases_the_connection_and_the_unit(execenv):
    # when
    outcome = await pin_block_raises(execenv)
    # then
    assert (outcome.released, outcome.in_flight) == ([True], 0)


async def test_a_failing_stream_close_does_not_stop_the_other_streams_from_closing(execenv):
    # when
    outcome = await exit_pin_with_two_open_streams(execenv)
    # then
    assert outcome.streams_open == 0


async def test_the_first_close_error_is_raised_once_every_stream_was_tried(execenv):
    # when
    outcome = await exit_pin_with_two_open_streams(execenv)
    # then
    assert (type(outcome.exit_error), str(outcome.exit_error)) == (ValueError, "close failed")


async def test_a_failing_stream_close_still_returns_the_connection_and_the_unit(execenv):
    # when
    outcome = await exit_pin_with_two_open_streams(execenv)
    # then
    assert (outcome.released, outcome.in_flight) == ([True], 0)


async def test_leaving_a_pin_from_another_task_raises_nothing(execenv):
    # when
    outcome = await leave_a_pin_from_another_task(execenv)
    # then
    assert outcome.error is None


async def test_leaving_a_pin_from_another_task_closes_it_and_releases_everything(execenv):
    # when
    outcome = await leave_a_pin_from_another_task(execenv)
    # then
    assert (outcome.executor_closed, outcome.in_flight, outcome.released) == (True, 0, [True])


async def test_an_exception_in_a_nested_pin_restores_the_depth(execenv):
    # when
    depth = await depth_after_a_nested_pin_raises()
    # then
    assert depth == 1


async def test_stream_on_a_closed_pin_raises_at_the_call(execenv):
    # when / then
    with pytest.raises(ConcurrentQueryError):
        await stream_of_a_closed_pin()
