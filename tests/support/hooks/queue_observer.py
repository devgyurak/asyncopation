"""`QueueObserver` (ADR-0005 Decision 1) scenarios: events drained by an async consumer, a full
queue, and events delivered outside the queue's own event loop (sequential loops in sync code, no
threads).
"""

from __future__ import annotations

import asyncio
from typing import Any

from tests.support.hooks.events import hooks, started_event
from tests.support.hooks.scenarios import unobserved_fetch


async def take(queue: Any, count: int) -> list[Any]:
    """The first `count` events of `async for event in queue` (then leave the loop)."""
    events: list[Any] = []
    async for event in queue:
        events.append(event)
        if len(events) == count:
            break
    return events


async def queue_after_one_fetch(maxsize: int = 1000) -> Any:
    """A `QueueObserver(maxsize)` that observed one borrowed `fetch_all` ("SELECT 1")."""
    queue = hooks().QueueObserver(maxsize)
    with hooks().observe(queue):
        await unobserved_fetch("SELECT 1")
    return queue


async def queue_after_global_fetch() -> Any:
    """A `QueueObserver` registered with `add_observer` while one `fetch_all` runs."""
    from tests.support.hooks.scenarios import registered

    queue = hooks().QueueObserver()
    with registered(queue):
        await unobserved_fetch("SELECT 1")
    return queue


async def _make_queue(maxsize: int) -> Any:
    return hooks().QueueObserver(maxsize)


def queue_of_a_finished_loop(maxsize: int = 10) -> Any:
    """A `QueueObserver` created inside an event loop that has since been closed (sync code)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_make_queue(maxsize))
    finally:
        loop.close()


async def _deliver(queue: Any, event: Any) -> Any:
    return queue(event)


def deliver_in_another_loop(queue: Any, event: Any | None = None) -> Any:
    """Call `queue(event)` while a second, different event loop is running (sync code)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_deliver(queue, event or started_event()))
    finally:
        loop.close()


def filled(queue: Any, count: int) -> list[Any]:
    """Call `queue(event)` with `count` distinct started events (SELECT 0, SELECT 1, ...); returns
    what each call returned."""
    return [queue(started_event(sql=f"SELECT {n}")) for n in range(count)]
