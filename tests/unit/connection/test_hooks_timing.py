"""ADR-0005 event timings (issue #4) on a fake clock: `queue_time` is the wait for a pooled
connection, `query_time` runs from after the `query_started` observers until the driver call (a
stream: its close) ends, and `total_time` is their sum - nothing is counted twice."""

import pytest

from tests.support.executors.ops import CONTROLS, STATEMENTS
from tests.support.hooks.clock import (
    CONSUMER,
    OBSERVER,
    OPEN,
    QUERY,
    QUEUE,
    install_fake_clock,
    timed_call,
    timed_stream,
)
from tests.support.hooks.scenarios import observed_call

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_pinned_call_has_no_queue_time(execenv, op):
    # when
    observed = await observed_call(op, pinned=True)
    # then
    assert (observed.started.queue_time, observed.finished.queue_time) == (0.0, 0.0)


@pytest.mark.parametrize("op", STATEMENTS)
async def test_a_borrowed_statement_queue_time_is_the_wait_for_a_connection(
    execenv, monkeypatch, op
):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, op, pinned=False)
    # then
    assert (observed.started.queue_time, observed.finished.queue_time) == (QUEUE, QUEUE)


@pytest.mark.parametrize("op", STATEMENTS)
@PINNED
async def test_query_time_is_the_time_spent_in_the_driver(execenv, monkeypatch, op, pinned):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, op, pinned=pinned)
    # then
    assert observed.finished.query_time == QUERY


@pytest.mark.parametrize("op", CONTROLS)
async def test_a_control_call_query_time_is_the_time_spent_in_the_driver(execenv, monkeypatch, op):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, op, pinned=True)
    # then
    assert (observed.finished.query_time, observed.finished.total_time) == (QUERY, QUERY)


@pytest.mark.parametrize("op", STATEMENTS)
async def test_a_borrowed_statement_total_time_is_queue_plus_query_time(execenv, monkeypatch, op):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, op, pinned=False)
    # then
    assert observed.finished.total_time == QUEUE + QUERY


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_pinned_call_total_time_is_its_query_time(execenv, monkeypatch, op):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, op, pinned=True)
    # then
    finished = observed.finished
    assert finished.total_time == finished.queue_time + finished.query_time == finished.query_time


@pytest.mark.parametrize(
    ("pinned", "queue"), [(False, QUEUE), (True, 0.0)], ids=["borrowed", "pinned"]
)
async def test_time_spent_in_a_started_observer_is_in_neither_queue_nor_query_time(
    execenv, monkeypatch, pinned, queue
):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_call(execenv, clock, "fetch_all", pinned=pinned, observer_time=OBSERVER)
    # then
    finished = observed.finished
    assert (finished.queue_time, finished.query_time, finished.total_time) == (
        queue,
        QUERY,
        queue + QUERY,
    )


@PINNED
async def test_stream_query_time_includes_the_consumer_time_between_chunks(
    execenv, monkeypatch, pinned
):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_stream(execenv, clock, pinned=pinned)
    # then
    assert observed.finished.query_time == OPEN + CONSUMER


async def test_a_borrowed_stream_queue_time_is_the_wait_for_a_connection(execenv, monkeypatch):
    # given
    clock = install_fake_clock(monkeypatch)
    # when
    observed = await timed_stream(execenv, clock, pinned=False)
    # then
    assert (observed.started.queue_time, observed.finished.total_time) == (
        QUEUE,
        QUEUE + OPEN + CONSUMER,
    )
