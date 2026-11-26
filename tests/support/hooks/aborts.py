"""Exceptions raised by observers on the executors (ADR-0005, review round 2): they never break a
pinned transaction, and on a failure path they never replace the original exception.

Every function only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, executor_module, executor_of
from tests.support.hooks.events import Raising, Recorder, hooks
from tests.support.hooks.scenarios import Observed, outcome_of
from tests.support.hooks.streams import CHUNK, ROWS, SQL, ConsumerError


def observer_note(late: BaseException) -> str:
    """The note an executor attaches to the original exception when a `query_finished` observer
    also raised `late`."""
    return f"asyncopation: a query_finished observer also raised {late!r}"


def notes_of(error: BaseException | None) -> list[str]:
    return list(getattr(error, "__notes__", []))


class RaisingEach:
    """An observer raising `started` on `query_started` and `finished` on `query_finished`."""

    def __init__(self, started: BaseException, finished: BaseException) -> None:
        self.errors = {"query_started": started, "query_finished": finished}

    def __call__(self, event: Any) -> None:
        raise self.errors[event.name]


# --------------------------------------------------------------------------- pinned transactions
@dataclass(frozen=True)
class InTransaction:
    error: BaseException | None
    broken: bool
    rollback_error: BaseException | None
    kinds: list[str]


async def observer_raising_in_transaction(env: ExecEnv, observer: Any, op: str) -> InTransaction:
    """`begin()` on a pin, then `op` observed by `observer`, then `rollback()`."""
    async with executor_module().pin(ALIAS) as executor:
        await executor.begin()
        with hooks().observe(observer):
            error = await raised_by(call(executor, op))
        broken = executor.broken
        rollback_error = await raised_by(executor.rollback())
        kinds = kinds_of(env.default)
    return InTransaction(error, broken, rollback_error, kinds)


async def real_cancel_in_observed_transaction(env: ExecEnv) -> tuple[bool, bool]:
    """The owner task of a pin in a transaction is cancelled while its statement is held in the
    driver, with an observer active: `(task cancelled, pin broken)`."""
    hold = env.default.hold_statements()
    seen: list[Any] = []

    async def worker() -> None:
        async with executor_module().pin(ALIAS) as executor:
            seen.append(executor)
            await executor.begin()
            await executor.fetch_all("SELECT 1", None)

    with hooks().observe(Recorder()):
        task = asyncio.create_task(worker())
        await hold.wait_entered()
        task.cancel()
        await asyncio.wait([task])
    return task.cancelled(), seen[0].broken


# --------------------------------------------------------------------------- original exception wins
async def failed_call_with_raising_observer(
    env: ExecEnv, op: str, late: BaseException, *, pinned: bool
) -> Observed:
    """`op` while the driver raises a queued error and a `query_finished` observer raises `late`."""
    injected = ValueError("driver failure")
    env.default.queue(injected)
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(recorder), hooks().observe(Raising("query_finished", late)):
        result, error = await outcome_of(run())
    return Observed(recorder, result, error, injected)


async def raising_on_both_events(
    observer: RaisingEach, op: str, *, pinned: bool
) -> BaseException | None:
    """`op` observed by one observer that raises on `query_started` and on `query_finished`."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(observer):
        return await raised_by(run())


async def consumer_error_with_raising_observer(
    env: ExecEnv, late: BaseException, *, pinned: bool
) -> BaseException | None:
    """The consumer's code raises inside the stream block while a `query_finished` observer raises
    `late` when the stream closes."""
    env.default.queue(list(ROWS))

    async def run() -> Any:
        async with executor_of(pinned) as executor, executor.stream(SQL, None, CHUNK) as chunks:
            await anext(chunks)
            raise ConsumerError("the consumer failed")

    with hooks().observe(Raising("query_finished", late)):
        return await raised_by(run())
