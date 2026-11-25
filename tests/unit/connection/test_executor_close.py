"""A pin counts as one in-flight unit; `close()` waits for pins and streams (ADR-0001, ADR-0004)."""

import pytest

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
