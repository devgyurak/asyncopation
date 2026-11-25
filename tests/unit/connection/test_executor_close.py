"""A pin counts as one in-flight unit; `close()` waits for pins and streams (ADR-0001, ADR-0004)."""

import pytest

from asyncopation import ConcurrentQueryError
from asyncopation.connection.executor import BorrowedExecutor
from tests.support.executors.close_pins import (
    close_gives_up_on_a_pin_whose_block_already_ended,
    pin_again_after_close_gave_up_on_the_pin,
    select_an_executor_after_close_gave_up_on_the_pin,
    use_a_pin_after_close_gave_up,
)
from tests.support.executors.ops import CONTROLS, STATEMENTS
from tests.support.executors.scenarios import (
    close_while_open,
    trace_pin_in_flight,
    use_while_closing,
)

UNITS = ["pin", "stream"]


async def test_a_pin_counts_as_one_unit_in_flight_while_idle(execenv):
    # when
    trace = await trace_pin_in_flight(execenv)
    # then
    assert (trace.before, trace.idle) == (0, 1)


async def test_statements_inside_a_pin_do_not_count_again(execenv):
    # when
    trace = await trace_pin_in_flight(execenv)
    # then
    assert (trace.inside_statement, trace.after_statement) == (1, 1)


async def test_the_pin_stops_counting_when_it_ends(execenv):
    # when
    trace = await trace_pin_in_flight(execenv)
    # then
    assert trace.after == 0


@pytest.mark.parametrize("kind", UNITS)
async def test_close_waits_while_a_pin_or_a_stream_is_open(execenv, kind):
    # when
    outcome = await close_while_open(execenv, kind, close_timeout=5.0, give_up=False)
    # then
    assert outcome.done_while_open is False
    assert outcome.in_flight_while_open == 1


@pytest.mark.parametrize("kind", UNITS)
async def test_close_finishes_once_the_open_unit_ended(execenv, kind):
    # when
    outcome = await close_while_open(execenv, kind, close_timeout=5.0, give_up=False)
    # then
    assert (outcome.error, outcome.exit_error) == (None, None)
    assert outcome.driver_closed is True


@pytest.mark.parametrize("kind", UNITS)
async def test_close_gives_up_after_its_timeout_and_raises_timeout_error(execenv, kind):
    # when
    outcome = await close_while_open(execenv, kind, close_timeout=0.05, give_up=True)
    # then
    assert isinstance(outcome.error, TimeoutError)


@pytest.mark.parametrize("kind", UNITS)
async def test_close_closes_the_driver_anyway_when_the_timeout_passed(execenv, kind):
    # when
    outcome = await close_while_open(execenv, kind, close_timeout=0.05, give_up=True)
    # then
    assert outcome.driver_closed is True


@pytest.mark.parametrize("kind", UNITS)
async def test_leaving_the_unit_after_a_timed_out_close_does_not_raise(execenv, kind):
    # when
    outcome = await close_while_open(execenv, kind, close_timeout=0.05, give_up=True)
    # then
    assert outcome.exit_error is None
    assert outcome.in_flight_after == 0


@pytest.mark.parametrize("kind", ["borrowed", "pin"])
async def test_a_closing_loop_refuses_new_borrows_with_a_runtime_error(execenv, held_close, kind):
    # when
    outcome = await use_while_closing(kind)
    # then
    assert type(outcome.error) is RuntimeError  # not a ConcurrentQueryError (also a RuntimeError)
    assert "is closing" in str(outcome.error)


@pytest.mark.parametrize("kind", ["borrowed", "pin"])
async def test_a_refused_borrow_does_not_leak_the_in_flight_counter(execenv, held_close, kind):
    # when
    outcome = await use_while_closing(kind)
    # then
    assert outcome.in_flight == 1  # only the unit that holds the close back


DRIVERS_CLOSED = "drivers were closed by close()"


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_an_operation_on_a_pin_close_gave_up_on_raises_concurrent_query_error(execenv, op):
    # when
    outcome = await use_a_pin_after_close_gave_up(execenv, op)
    # then
    assert type(outcome.error) is ConcurrentQueryError
    assert DRIVERS_CLOSED in str(outcome.error)


async def test_a_pin_close_gave_up_on_is_closed(execenv):
    # when
    outcome = await use_a_pin_after_close_gave_up(execenv, "fetch_all")
    # then
    assert isinstance(outcome.close_error, TimeoutError)
    assert outcome.closed is True


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_refused_operation_on_a_pin_close_gave_up_on_never_reaches_the_driver(execenv, op):
    # when
    outcome = await use_a_pin_after_close_gave_up(execenv, op)
    # then
    assert outcome.kinds == []


async def test_leaving_a_pin_close_gave_up_on_releases_its_connection_and_unit(execenv):
    # when
    outcome = await use_a_pin_after_close_gave_up(execenv, "fetch_all")
    # then
    assert outcome.exit_error is None
    assert (outcome.in_flight, outcome.released) == (0, [True])


async def test_the_owner_still_gets_its_pin_from_current_executor_after_close_gave_up(execenv):
    # when
    outcome = await select_an_executor_after_close_gave_up_on_the_pin()
    # then
    assert outcome.current is outcome.pin


async def test_that_pin_refuses_the_owners_statement_after_close_gave_up(execenv):
    # when
    outcome = await select_an_executor_after_close_gave_up_on_the_pin()
    # then
    assert isinstance(outcome.error, ConcurrentQueryError)


async def test_the_owner_borrows_again_once_the_block_it_lost_ended(execenv):
    # when
    outcome = await select_an_executor_after_close_gave_up_on_the_pin()
    # then
    assert isinstance(outcome.after_exit, BorrowedExecutor)


async def test_a_nested_block_of_the_alias_is_refused_after_close_gave_up_on_the_pin(execenv):
    # when
    error = await pin_again_after_close_gave_up_on_the_pin()
    # then
    assert isinstance(error, ConcurrentQueryError)


async def test_close_does_not_flag_a_pin_whose_block_already_ended(execenv):
    # when
    drivers_closed = await close_gives_up_on_a_pin_whose_block_already_ended()
    # then
    assert drivers_closed is False
