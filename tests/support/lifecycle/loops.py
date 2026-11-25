"""Second event loops: threads, live loops, loops closed without `close()`."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from tests.support.lifecycle.helpers import aliases_of


async def in_flight_in_other_loop(alias: str) -> int:
    from asyncopation.connection import registry

    async def probe() -> int:
        return registry.in_flight(alias)

    return await run_in_thread_loop(probe())


def fresh_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    loop.close()
    return loop


@dataclass
class SecondLoop:
    driver: Any
    loop: asyncio.AbstractEventLoop
    entries_inside: list[tuple[str, Any]]
    entries_after_close: list[tuple[str, Any]]


async def open_in_second_loop(alias: str = "default") -> SecondLoop:
    """Open `alias` in a new event loop on a thread, then close that loop's drivers."""

    async def run() -> SecondLoop:
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close, ensure_driver

        driver = await ensure_driver(alias)
        inside = registry.entries()
        await close()
        return SecondLoop(driver, asyncio.get_running_loop(), inside, registry.entries())

    return await run_in_thread_loop(run())


async def leave_open_in_other_loop(alias: str = "default") -> None:
    """Open `alias` in a throwaway loop on a thread and return WITHOUT closing it (a leak)."""
    from asyncopation.connection.lifecycle import ensure_driver

    async def run() -> None:
        await ensure_driver(alias)

    await run_in_thread_loop(run())


async def run_in_thread_loop(coroutine: Any, within: float = 15.0) -> Any:
    """Run `coroutine` to completion in a new event loop on a worker thread (bounded)."""
    async with asyncio.timeout(within):
        return await asyncio.to_thread(asyncio.run, coroutine)


class ThreadLoop:
    """An event loop that keeps running on its own thread (a second, live loop)."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> ThreadLoop:
        self.thread.start()
        return self

    async def run(self, coroutine: Any, within: float = 15.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        async with asyncio.timeout(within):
            return await asyncio.wrap_future(future)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


async def hold_driver_in_live_loop(thread_loop: ThreadLoop, alias: str = "default") -> None:
    from asyncopation.connection.lifecycle import ensure_driver

    await thread_loop.run(ensure_driver(alias))


async def release_live_loop(thread_loop: ThreadLoop) -> None:
    """Close that loop's drivers (in it) and stop it."""
    from asyncopation.connection.lifecycle import close

    await thread_loop.run(close())
    thread_loop.stop()


class ClosedLoopState:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def closing_mark_remains(self) -> bool:
        from asyncopation.connection import registry

        return self.loop in registry._closing_loops

    def flights_remain(self) -> bool:
        from asyncopation.connection import registry

        return any(key[1] is self.loop for key in registry._flights)


async def leave_closing_and_flight_in_a_closed_loop(alias: str = "default") -> ClosedLoopState:
    """In a loop that is then closed: open `alias`, start a statement and begin closing."""
    from asyncopation.connection import registry
    from asyncopation.connection.lifecycle import ensure_driver

    async def run() -> ClosedLoopState:
        await ensure_driver(alias)
        registry.statement_started(alias)
        registry.begin_closing(asyncio.get_running_loop())
        return ClosedLoopState(asyncio.get_running_loop())

    return await run_in_thread_loop(run())


def purge_and_report_leaks() -> list[str]:
    """Purge closed loops, return (and so consume) the leaked entries' aliases."""
    from asyncopation.connection import registry

    registry.purge_closed_loops()
    return aliases_of(registry.take_leaked())


async def purge_from_threads(count: int = 4) -> list[str]:
    """`registry.purge_closed_loops()` from `count` threads at once; the errors they raised."""
    from asyncopation.connection import registry

    errors: list[str] = []
    barrier = threading.Barrier(count)

    def worker() -> None:
        barrier.wait()
        try:
            registry.purge_closed_loops()
        except Exception as exc:
            errors.append(repr(exc))

    await asyncio.gather(*(asyncio.to_thread(worker) for _ in range(count)))
    return errors
