"""Interleavings of open / close / setup, driven deterministically (no sleeps)."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Literal

from tests.support.lifecycle.fakes import FAKE_ENGINE, FakeEnv, Recorder


async def ensure_many(alias: str, count: int) -> list[Any]:
    from asyncopation.connection.lifecycle import ensure_driver

    return await asyncio.gather(*(ensure_driver(alias) for _ in range(count)))


async def open_then_close(alias: str = "default") -> None:
    from asyncopation.connection.lifecycle import close, ensure_driver

    await ensure_driver(alias)
    await close()


async def ensure_is_refused(alias: str) -> bool:
    from asyncopation.connection.lifecycle import ensure_driver

    try:
        await ensure_driver(alias)
    except RuntimeError:
        return True
    return False


async def _is_closing() -> bool:
    from asyncopation.connection import registry

    return registry.is_closing(asyncio.get_running_loop())


class ClosingScenario:
    """A `close()` that is held back by one statement in flight on `alias`."""

    def __init__(self, alias: str, timeout: float) -> None:
        self.alias = alias
        self.timeout = timeout
        self.task: asyncio.Task[None] | None = None

    async def begin(self) -> None:
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close
        from tests.support.aio import wait_until

        registry.statement_started(self.alias)
        self.task = asyncio.create_task(close(timeout=self.timeout))
        await wait_until(_is_closing)

    async def join(self) -> None:
        assert self.task is not None
        await self.task

    async def cancel_close(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def finish(self) -> None:
        from asyncopation.connection import registry

        with contextlib.suppress(Exception):
            registry.statement_finished(self.alias)
        await self.join()

    async def cleanup(self) -> None:
        with contextlib.suppress(BaseException):
            await self.finish()


@dataclass
class LockCounter:
    created: int = 0
    acquired: int = 0


def count_locks(monkeypatch: Any) -> LockCounter:
    counter = LockCounter()

    class CountingLock(asyncio.Lock):
        def __init__(self) -> None:
            super().__init__()
            counter.created += 1

        async def acquire(self) -> Literal[True]:
            counter.acquired += 1
            return await super().acquire()

    monkeypatch.setattr(asyncio, "Lock", CountingLock)
    return counter


async def ensure_many_settled(alias: str, count: int) -> list[Any]:
    """Like `ensure_many` but errors are returned in the list instead of raised."""
    from asyncopation.connection.lifecycle import ensure_driver

    return await asyncio.gather(
        *(ensure_driver(alias) for _ in range(count)), return_exceptions=True
    )


async def setup_and_ensure_concurrently(alias: str) -> tuple[None, Any]:
    from asyncopation.connection.lifecycle import ensure_driver, setup

    return await asyncio.gather(setup([alias]), ensure_driver(alias))


async def _two_acquires(counter: LockCounter) -> bool:
    return counter.acquired >= 2


async def _three_acquires(counter: LockCounter) -> bool:
    return counter.acquired >= 3


async def _opener_started(recorder: Recorder) -> bool:
    return recorder.open_attempts >= 1


class OpeningScenario:
    """A first `ensure_driver` that is held inside `driver.open()` by the script's gate."""

    def __init__(
        self, env: FakeEnv, alias: str = "default", counter: LockCounter | None = None
    ) -> None:
        self.env = env
        self.alias = alias
        self.counter = counter  # counts lock acquisitions: a second one means a parked waiter
        self.task: asyncio.Task[Any] | None = None

    async def begin(self) -> None:
        from asyncopation.connection.lifecycle import ensure_driver
        from tests.support.aio import wait_until

        self.env.script.gate = asyncio.Event()
        self.task = asyncio.create_task(ensure_driver(self.alias))
        await wait_until(lambda: _opener_started(self.env.recorder))

    async def cancel(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.env.script.gate = None

    async def finish(self) -> Any:
        assert self.task is not None
        gate, self.env.script.gate = self.env.script.gate, None
        if gate is not None:
            gate.set()
        return await self.task

    async def cleanup(self) -> None:
        with contextlib.suppress(BaseException):
            await self.finish()


async def reopen_after_cancelled_first_opener(scenario: OpeningScenario) -> Any:
    """Cancel the opener stuck in `open()`, then let the next caller open the driver."""
    from asyncopation.connection.lifecycle import ensure_driver

    await scenario.cancel()
    return await ensure_driver(scenario.alias)


async def close_while_opening(scenario: OpeningScenario) -> list[Any]:
    """`close()` while the first open is held in `driver.open()`; then let that open finish."""
    from asyncopation.connection.lifecycle import close
    from tests.support.aio import wait_until

    assert scenario.task is not None
    close_task = asyncio.create_task(close())
    await wait_until(_is_closing)
    gate, scenario.env.script.gate = scenario.env.script.gate, None
    assert gate is not None
    gate.set()
    return list(await asyncio.gather(scenario.task, close_task, return_exceptions=True))


async def waiter_meets_closing(scenario: OpeningScenario) -> list[Any]:
    """A second caller queues on the open lock, `close()` begins, then the open is released."""
    from asyncopation.connection.lifecycle import close, ensure_driver
    from tests.support.aio import wait_until

    assert scenario.task is not None
    assert scenario.counter is not None
    counter = scenario.counter
    waiter = asyncio.create_task(ensure_driver(scenario.alias))
    await wait_until(lambda: _two_acquires(counter))  # the waiter is parked behind the opener
    close_task = asyncio.create_task(close())
    await wait_until(_is_closing)
    gate, scenario.env.script.gate = scenario.env.script.gate, None
    assert gate is not None
    gate.set()
    return list(await asyncio.gather(scenario.task, waiter, close_task, return_exceptions=True))


class SetupScenario:
    """`setup(aliases)` held inside the second `driver.open()` (the first one succeeded)."""

    def __init__(self, env: FakeEnv, aliases: list[str]) -> None:
        self.env = env
        self.aliases = aliases
        self.task: asyncio.Task[None] | None = None

    async def begin(self) -> None:
        from asyncopation.connection.lifecycle import setup
        from tests.support.aio import wait_until

        self.env.script.gate = asyncio.Event()
        self.env.script.gate_after = 1
        self.task = asyncio.create_task(setup(self.aliases))
        await wait_until(lambda: _second_open_started(self.env.recorder))

    async def cancel(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.env.script.gate = None


async def _second_open_started(recorder: Recorder) -> bool:
    return recorder.open_attempts >= 2


class OverlappingClose:
    """Close B waits for a statement in flight; close A (short timeout) starts later and ends first."""

    def __init__(self, alias: str = "default") -> None:
        self.alias = alias
        self.b: asyncio.Task[None] | None = None

    async def begin(self) -> list[Any]:
        """Start B, then run A to its timeout; returns A's outcome."""
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close
        from tests.support.aio import wait_until

        registry.statement_started(self.alias)
        self.b = asyncio.create_task(close(timeout=5.0))
        await wait_until(_is_closing)
        return list(await asyncio.gather(close(timeout=0.05), return_exceptions=True))

    async def finish(self) -> None:
        from asyncopation.connection import registry

        with contextlib.suppress(Exception):
            registry.statement_finished(self.alias)
        if self.b is not None:
            await asyncio.gather(self.b, return_exceptions=True)


class ConcurrentOpenScenario:
    """Task B holds `other`'s open lock (its driver is gated); `setup([default, other, third])`
    opens `default`, parks behind B, and fails on `third` once B has opened `other`."""

    def __init__(self, env: FakeEnv, counter: LockCounter, within: float = 5.0) -> None:
        self.env = env
        self.counter = counter
        self.within = within  # how long each wait may take before the scenario gives up
        self.b: asyncio.Task[Any] | None = None
        self.setup_task: asyncio.Task[None] | None = None

    async def run(self) -> list[Any]:
        from asyncopation.connection import configure
        from asyncopation.connection.lifecycle import ensure_driver, setup
        from tests.support.aio import wait_until

        script = self.env.script
        self.env.add_alias("third", "fake_third_db", FAKE_ENGINE)
        configure()  # the new alias is part of the configuration
        script.gate = asyncio.Event()
        script.gate_driver_index = 0  # B's driver is the first one built
        script.fail_open_after = 2  # the third open (after default and other) fails
        try:
            self.b = asyncio.create_task(ensure_driver("other"))
            await wait_until(lambda: _opener_started(self.env.recorder), within=self.within)
            self.setup_task = asyncio.create_task(setup(["default", "other", "third"]))
            await wait_until(
                lambda: _three_acquires(self.counter), within=self.within
            )  # setup is parked behind B
            # ordering: `default` is open (setup opened it), B's `other` is still gated
            assert self.env.recorder.opens == 1
            script.gate.set()
            script.gate = None
            return list(await asyncio.gather(self.b, self.setup_task, return_exceptions=True))
        finally:
            await self._cleanup()

    def tasks_stopped(self) -> bool:
        tasks = (self.b, self.setup_task)
        return all(task is not None and task.done() for task in tasks)

    async def _cleanup(self) -> None:
        """Release the gate and stop both tasks (also when a wait above timed out)."""
        gate, self.env.script.gate = self.env.script.gate, None
        if gate is not None:
            gate.set()
        tasks = [t for t in (self.b, self.setup_task) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _four_acquires(counter: LockCounter) -> bool:
    return counter.acquired >= 4


async def _rollback_is_closing(recorder: Recorder) -> bool:
    return recorder.closes >= 1


class RollbackScenario:
    """`setup([default, third])` opens `default`, fails on `third` and rolls back; the rollback's
    `driver.close()` is held by the script's close gate so that others can interleave."""

    def __init__(self, env: FakeEnv, counter: LockCounter) -> None:
        self.env = env
        self.counter = counter
        self.setup_task: asyncio.Task[None] | None = None
        self.other: asyncio.Task[Any] | None = None

    async def begin(self) -> None:
        from asyncopation.connection import configure
        from asyncopation.connection.lifecycle import setup
        from tests.support.aio import wait_until

        script = self.env.script
        self.env.add_alias("third", "fake_third_db", FAKE_ENGINE)
        configure()
        script.fail_open_after = 1  # `third` fails once `default` is open
        script.close_gate = asyncio.Event()
        self.setup_task = asyncio.create_task(setup(["default", "third"]))
        await wait_until(lambda: _rollback_is_closing(self.env.recorder))

    async def concurrent_ensure(self) -> list[Any]:
        """A caller queues on the entry being rolled back, then the rollback's close finishes."""
        from asyncopation.connection.lifecycle import ensure_driver
        from tests.support.aio import wait_until

        assert self.setup_task is not None
        self.other = asyncio.create_task(ensure_driver("default"))
        # ordering: setup default(1), setup third(2), rollback close(3), this caller(4)
        await wait_until(lambda: _four_acquires(self.counter))
        self.release()
        return list(await asyncio.gather(self.setup_task, self.other, return_exceptions=True))

    async def cancel_setup(self) -> None:
        assert self.setup_task is not None
        self.setup_task.cancel()
        await asyncio.gather(self.setup_task, return_exceptions=True)

    def release(self) -> None:
        if self.env.script.close_gate is not None:
            self.env.script.close_gate.set()

    def entry_forgotten(self, alias: str = "default") -> bool:
        from asyncopation.connection import registry

        return registry.entry(alias, asyncio.get_running_loop()) is None

    async def cleanup(self) -> None:
        self.release()
        tasks = [t for t in (self.setup_task, self.other) if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # let a shielded close finish


async def opener_is_the_opening_task(alias: str = "default") -> bool:
    """`entry.opener()` is the task that ran `open()` (here: a task created just for it)."""
    from asyncopation.connection import registry
    from asyncopation.connection.lifecycle import ensure_driver

    task = asyncio.create_task(ensure_driver(alias))
    await task
    entry = registry.entry(alias, asyncio.get_running_loop())
    assert entry is not None
    return entry.opener is not None and entry.opener() is task


@dataclass
class HungClose:
    elapsed: float
    close_error: BaseException | None
    opener_outcome: Any


async def close_while_open_hangs(scenario: OpeningScenario, close_timeout: float) -> HungClose:
    """`close(close_timeout)` while the first open hangs in `driver.open()` for good.

    A generous upper bound (10 s) keeps a regression (an unbounded wait) from hanging the suite.
    """
    from asyncopation.connection.lifecycle import close

    loop = asyncio.get_running_loop()
    started = loop.time()
    error: BaseException | None = None
    try:
        async with asyncio.timeout(10.0):
            await close(timeout=close_timeout)
    except BaseException as exc:
        error = exc
    elapsed = loop.time() - started
    assert scenario.task is not None
    outcome = (await asyncio.gather(scenario.task, return_exceptions=True))[0]
    return HungClose(elapsed, error, outcome)
