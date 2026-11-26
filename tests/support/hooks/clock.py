"""A fake, manually advanced clock for exact ADR-0005 timings (no real sleeps).

`hooks.perf_counter` and `executor.perf_counter` are module-level names, so both are replaced; the
scenarios advance the clock while the pool hands out a connection (queue time), inside the driver
call (query time), inside a `query_started` observer, or in the consumer between chunks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call
from tests.support.executors.scenarios import executor_of
from tests.support.hooks.events import STARTED, Recorder, hooks
from tests.support.hooks.scenarios import Observed

QUEUE = 2.0
QUERY = 3.0
OBSERVER = 7.0
OPEN = 1.0
CONSUMER = 4.0


class FakeClock:
    """Returns `now` until `advance()` moves it (whole seconds: exact float arithmetic)."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def advancing(self, seconds: float) -> Callable[[], Awaitable[None]]:
        """An async callable that advances the clock (a `GatedDriver.during` hook)."""

        async def spend() -> None:
            self.advance(seconds)

        return spend


def install_fake_clock(monkeypatch: Any) -> FakeClock:
    from asyncopation.connection import executor

    clock = FakeClock()
    monkeypatch.setattr(hooks(), "perf_counter", clock)
    monkeypatch.setattr(executor, "perf_counter", clock)
    return clock


class SlowStartedObserver:
    """Spends `seconds` of the fake clock on every `query_started` event."""

    def __init__(self, clock: FakeClock, seconds: float) -> None:
        self.clock = clock
        self.seconds = seconds

    def __call__(self, event: Any) -> None:
        if event.name == STARTED:
            self.clock.advance(self.seconds)


async def _with_held_borrow(env: ExecEnv, clock: FakeClock, run: Callable[[], Any]) -> Any:
    """Run `run()` in a task (it copies the caller's context) whose borrow waits `QUEUE` seconds."""
    hold = env.default.hold_borrows()
    task = asyncio.create_task(run())
    await hold.wait_entered()
    clock.advance(QUEUE)
    hold.release()
    return await task


async def timed_call(
    env: ExecEnv, clock: FakeClock, op: str, *, pinned: bool, observer_time: float = 0.0
) -> Observed:
    """`op` whose borrow waits `QUEUE` seconds (borrowed only) and whose driver call (statement or
    transaction control) takes `QUERY` seconds; a second observer spends `observer_time` on
    `query_started`."""
    env.default.during = clock.advancing(QUERY)
    env.default.control_during = clock.advancing(QUERY)
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(SlowStartedObserver(clock, observer_time)), hooks().observe(recorder):
        if pinned:
            result = await run()
        else:
            result = await _with_held_borrow(env, clock, run)
    return Observed(recorder, result)


async def timed_stream(env: ExecEnv, clock: FakeClock, *, pinned: bool) -> Observed:
    """A stream that takes `OPEN` seconds to open; the consumer spends `CONSUMER` seconds between
    its two chunks before closing it."""
    env.default.queue([(n,) for n in range(1, 26)])
    env.default.during = clock.advancing(OPEN)
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor, executor.stream("SELECT 1", None, 10) as chunks:
            first = await anext(chunks)
            clock.advance(CONSUMER)
            return [first, await anext(chunks)]

    with hooks().observe(recorder):
        if pinned:
            result = await run()
        else:
            result = await _with_held_borrow(env, clock, run)
    return Observed(recorder, result)
