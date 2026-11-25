"""How a pin block ends when something goes wrong: nothing leaks and close() never waits for it."""

from django.core.exceptions import ImproperlyConfigured

from tests.support.executors.teardown import (
    exit_pin_with_two_open_streams,
    pin_block_raises,
    pin_unknown_alias,
    pin_when_borrow_fails,
)


async def test_a_pin_whose_connection_cannot_be_borrowed_raises_that_error(execenv):
    # when
    outcome = await pin_when_borrow_fails(execenv)
    # then
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
