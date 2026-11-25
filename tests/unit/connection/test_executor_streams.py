"""Streams of both executors: the connection and the in-flight unit live as long as the block
(ADR-0001 Decision 7)."""

import pytest

from asyncopation import ConcurrentQueryError
from tests.support.executors.scenarios import (
    borrowed_stream_use,
    close_stream_from_child,
    read_chunk_in_child,
    stream_in_pin,
    stream_left_open_at_pin_exit,
)
from tests.support.executors.teardown import (
    close_stream_slowly,
    enter_stream_after_block,
    enter_stream_in_child,
    rollback_with_open_stream,
    unentered_stream_then_statement,
)

LEAVES = ["ok", "error", "cancel"]


async def test_borrowed_stream_holds_the_connection_and_one_unit_while_open(execenv):
    # when
    outcome = await borrowed_stream_use(execenv, leave_with="ok")
    # then
    assert (outcome.in_flight_open, outcome.released_open) == (1, [False])


@pytest.mark.parametrize("leave_with", LEAVES)
async def test_borrowed_stream_releases_the_connection_however_the_block_ends(execenv, leave_with):
    # when
    outcome = await borrowed_stream_use(execenv, leave_with=leave_with)
    # then
    assert (outcome.in_flight_after, outcome.released_after) == (0, [True])


@pytest.mark.parametrize("leave_with", LEAVES)
async def test_leaving_a_borrowed_stream_midway_leaves_no_stream_open(execenv, leave_with):
    # when
    outcome = await borrowed_stream_use(execenv, leave_with=leave_with)
    # then
    assert outcome.streams_open_after == 0


async def test_a_stream_inside_a_pin_does_not_count_as_another_unit(execenv):
    # when
    outcome = await stream_in_pin(execenv)
    # then
    assert (outcome.in_flight_open, outcome.in_flight_after) == (1, 1)


async def test_the_pin_runs_statements_again_after_its_stream_closed(execenv):
    # when
    outcome = await stream_in_pin(execenv)
    # then
    assert outcome.statement_after == [(1,)]


@pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
async def test_closing_a_stream_from_another_task_never_raises(execenv, pinned):
    # when
    outcome = await close_stream_from_child(execenv, pinned=pinned)
    # then
    assert outcome.error is None


@pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
async def test_a_stream_closed_from_another_task_is_really_closed(execenv, pinned):
    # when
    outcome = await close_stream_from_child(execenv, pinned=pinned)
    # then
    assert outcome.streams_open == 0


async def test_a_borrowed_stream_closed_from_another_task_releases_its_unit(execenv):
    # when
    outcome = await close_stream_from_child(execenv, pinned=False)
    # then
    assert outcome.in_flight_after == 0


async def test_reading_a_pinned_stream_from_another_task_raises(execenv):
    # when / then
    with pytest.raises(ConcurrentQueryError):
        await read_chunk_in_child(execenv)


async def test_leaving_the_pin_closes_a_stream_that_is_still_open(execenv):
    # when
    outcome = await stream_left_open_at_pin_exit(execenv)
    # then
    assert (outcome.streams_open, outcome.exit_error) == (0, None)


async def test_reading_a_stream_after_the_pin_ended_raises_concurrent_query_error(execenv):
    # when
    outcome = await stream_left_open_at_pin_exit(execenv)
    # then
    assert isinstance(outcome.read_error, ConcurrentQueryError)


async def test_closing_a_stream_after_the_pin_ended_does_not_raise(execenv):
    # when
    outcome = await stream_left_open_at_pin_exit(execenv)
    # then
    assert outcome.late_close_error is None
    assert outcome.in_flight_after == 0


async def test_a_stream_that_was_created_but_never_entered_does_not_make_the_pin_busy(execenv):
    # when
    rows = await unentered_stream_then_statement(execenv)
    # then
    assert rows == [(1,)]


async def test_entering_a_pinned_stream_from_another_task_raises(execenv):
    # when
    error = await enter_stream_in_child()
    # then
    assert isinstance(error, ConcurrentQueryError)


async def test_entering_a_pinned_stream_after_the_block_exited_raises(execenv):
    # when
    error = await enter_stream_after_block()
    # then
    assert isinstance(error, ConcurrentQueryError)


async def test_rollback_with_a_stream_still_open_does_not_raise_busy(execenv):
    # when
    outcome = await rollback_with_open_stream(execenv)
    # then
    assert outcome.error is None


async def test_rollback_with_a_stream_still_open_closes_the_stream(execenv):
    # when
    outcome = await rollback_with_open_stream(execenv)
    # then
    assert outcome.streams_open == 0
    assert isinstance(outcome.read_error, ConcurrentQueryError)


async def test_rollback_with_a_stream_still_open_closes_it_before_rolling_back(execenv):
    # when
    outcome = await rollback_with_open_stream(execenv)
    # then
    assert outcome.kinds == ["begin", "stream", "rollback"]


async def test_a_second_close_waits_for_the_close_under_way(execenv):
    # when
    outcome = await close_stream_slowly(execenv)
    # then
    assert outcome.second_close_done is False
    assert outcome.errors == [None, None, None, None]


async def test_the_pin_does_not_return_its_connection_while_a_stream_is_closing(execenv):
    # when
    outcome = await close_stream_slowly(execenv)
    # then
    assert (outcome.released_meanwhile, outcome.released_after) == ([False], [True])
