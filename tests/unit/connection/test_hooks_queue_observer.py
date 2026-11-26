"""`QueueObserver` (issue #4, ADR-0005 Decision 1): a bounded-queue observer for async consumers.
It never blocks and schedules nothing; a full queue drops the incoming event and counts it, and so
does an event delivered outside the event loop the queue was created in."""

import asyncio
import inspect

import pytest

from asyncopation.connection import hooks
from tests.support.hooks.events import BOTH, STARTED, started_event
from tests.support.hooks.queue_observer import (
    consumer_woken_by_a_query,
    deliver_in_another_loop,
    filled,
    queue_after_global_fetch,
    queue_after_one_fetch,
    queue_of_a_finished_loop,
    take,
)


def test_creating_a_queue_observer_outside_a_running_loop_raises_runtime_error():
    # when / then
    with pytest.raises(RuntimeError):
        hooks.QueueObserver()


@pytest.mark.parametrize(
    "maxsize",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(1.5, id="float"),
        pytest.param("10", id="string"),
        pytest.param(None, id="none"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
    ],
)
async def test_a_queue_observer_with_an_invalid_maxsize_raises_value_error(maxsize):
    # when / then
    with pytest.raises(ValueError, match="maxsize"):
        hooks.QueueObserver(maxsize)


async def test_a_new_queue_observer_is_empty_and_has_dropped_nothing():
    # when
    queue = hooks.QueueObserver()
    # then
    assert (queue.empty(), queue.qsize(), queue.dropped) == (True, 0, 0)


async def test_observed_events_arrive_in_order_through_get(execenv):
    # given
    queue = await queue_after_one_fetch()
    # when
    first = await queue.get()
    second = await queue.get()
    # then
    assert [first.name, second.name] == BOTH
    assert (first.sql, second.sql) == ("SELECT 1", "SELECT 1")


async def test_observed_events_arrive_in_order_through_get_nowait(execenv):
    # given
    queue = await queue_after_one_fetch()
    # when
    first = queue.get_nowait()
    second = queue.get_nowait()
    # then
    assert [first.name, second.name] == BOTH


async def test_observed_events_arrive_in_order_through_async_for(execenv):
    # given
    queue = await queue_after_one_fetch()
    # when
    events = await take(queue, 2)
    # then
    assert [events[0].name, events[1].name] == BOTH


async def test_get_nowait_on_an_empty_queue_observer_raises_queue_empty(execenv):
    # given
    queue = await queue_after_one_fetch()
    queue.get_nowait()
    queue.get_nowait()
    # when / then
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


async def test_a_globally_registered_queue_observer_receives_the_events(execenv):
    # when
    queue = await queue_after_global_fetch()
    # then
    assert (queue.qsize(), queue.dropped) == (2, 0)


async def test_a_full_queue_observer_drops_the_incoming_event_and_counts_it(execenv):
    # when
    queue = await queue_after_one_fetch(maxsize=1)
    # then
    assert (queue.qsize(), queue.dropped) == (1, 1)


async def test_a_full_queue_observer_keeps_the_oldest_event(execenv):
    # given
    queue = await queue_after_one_fetch(maxsize=1)
    # when
    kept = queue.get_nowait()
    # then
    assert kept.name == STARTED


async def test_a_full_queue_observer_returns_immediately_on_every_further_event():
    # given
    queue = hooks.QueueObserver(2)
    # when
    returned = filled(queue, 5)
    # then
    assert returned == [None] * 5
    assert (queue.qsize(), queue.dropped) == (2, 3)


async def test_a_full_queue_observer_keeps_the_first_events_in_order():
    # given
    queue = hooks.QueueObserver(2)
    filled(queue, 5)
    # when
    events = await take(queue, 2)
    # then
    assert [events[0].sql, events[1].sql] == ["SELECT 0", "SELECT 1"]


def test_an_event_delivered_with_no_running_loop_is_dropped_and_counted():
    # given
    queue = queue_of_a_finished_loop()
    # when
    returned = queue(started_event())
    # then
    assert returned is None
    assert (queue.qsize(), queue.dropped) == (0, 1)


def test_an_event_delivered_in_another_event_loop_is_dropped_and_counted():
    # given
    queue = queue_of_a_finished_loop()
    # when
    deliver_in_another_loop(queue)
    # then
    assert (queue.qsize(), queue.dropped) == (0, 1)


def test_the_queue_observer_is_a_synchronous_callable():
    # when
    is_coroutine_function = inspect.iscoroutinefunction(hooks.QueueObserver.__call__)
    # then
    assert is_coroutine_function is False


async def test_a_default_queue_observer_drops_the_1001st_event():
    # given
    queue = hooks.QueueObserver()
    # when
    filled(queue, 1001)
    # then
    assert (queue.qsize(), queue.dropped) == (1000, 1)


async def test_a_consumer_waiting_in_get_before_a_query_is_woken_by_its_events(execenv):
    # when
    waiting, event = await consumer_woken_by_a_query()
    # then
    assert waiting is True
    assert (event.name, event.sql) == (STARTED, "SELECT 1")
