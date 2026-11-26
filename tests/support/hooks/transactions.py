"""Pinned transaction state under observers (ADR-0005 with ADR-0001 6c, review round 3): only what
the driver itself raised may break a transaction, and a control call an observer stopped before
the driver changed no transaction state.

Every function only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import CONTROLS, STATEMENTS, call, kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, _executors, executor_of
from tests.support.hooks.aborts import notes_of
from tests.support.hooks.events import STARTED, Raising, Recorder, hooks
from tests.support.hooks.scenarios import Observed, _outcome
from tests.support.hooks.streams import CHUNK, ROWS, SQL

EVERY_CALL = [
    *[pytest.param(op, False, id=f"borrowed-{op}") for op in STATEMENTS],
    *[pytest.param(op, True, id=f"pinned-{op}") for op in [*STATEMENTS, *CONTROLS]],
]
"""`(op, pinned)` for every statement on both executors and every control call on a pin."""


class Reraiser:
    """A `query_finished` observer that re-raises the call's own error (`raise event.error`)."""

    def __call__(self, event: Any) -> None:
        if event.name == "query_finished" and event.error is not None:
            raise event.error


def another_observer_note(event_name: str, late: BaseException) -> str:
    """The note attached to the first exception when a second observer of the same event raised."""
    return f"asyncopation: another observer of {event_name} also raised {late!r}"


# --------------------------------------------------------------------------- re-raising observers
@dataclass(frozen=True)
class Reraised:
    error: BaseException | None
    notes: list[str]
    broken: bool
    cancelled: bool


async def reraise_real_cancellation(env: ExecEnv, op: str) -> Reraised:
    """In a transaction, the driver call of `op` (`fetch_all`: the statement; `stream`: a chunk
    read) is held and its task cancelled; a `query_finished` observer re-raises that error. The
    worker catches what propagates, to report it."""
    hold = env.default.hold_statements() if op == "fetch_all" else env.default.hold_chunks()
    env.default.queue(list(ROWS))
    seen: dict[str, Any] = {}

    async def run(executor: Any) -> Any:
        if op == "fetch_all":
            return await executor.fetch_all("SELECT 1", None)
        async with executor.stream(SQL, None, CHUNK) as chunks:
            return await anext(chunks)

    async def worker() -> None:
        async with _executors().pin(ALIAS) as executor:
            await executor.begin()
            with hooks().observe(Reraiser()):
                seen["error"] = await raised_by(run(executor))
            seen["broken"] = executor.broken

    task = asyncio.create_task(worker())
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    error = seen["error"]
    return Reraised(
        error, notes_of(error), seen["broken"], isinstance(error, asyncio.CancelledError)
    )


async def reraise_driver_error(env: ExecEnv, op: str, *, pinned: bool) -> Observed:
    """The driver raises a queued error for `op`; a `query_finished` observer re-raises it."""
    injected = ValueError("driver failure")
    env.default.queue(injected)

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    recorder = Recorder()
    with hooks().observe(Reraiser()), hooks().observe(recorder):
        result, error = await _outcome(run())
    return Observed(recorder, result, error, injected)


async def cancellation_from_another_call_escapes_a_stream(env: ExecEnv) -> tuple[Any, bool]:
    """In a transaction, a pinned stream opened WITHOUT observers; inside its block an observed
    fetch on alias `other` is cancelled by its `query_started` observer, and that CancelledError
    leaves the stream block. Returns `(error, pin broken)`."""
    env.default.queue(list(ROWS))
    error = asyncio.CancelledError()

    async with _executors().pin(ALIAS) as executor:
        await executor.begin()

        async def body() -> None:
            async with executor.stream(SQL, None, CHUNK) as chunks:
                await anext(chunks)
                with hooks().observe(Raising(STARTED, error)):
                    await _executors().BorrowedExecutor("other").fetch_all("SELECT 1", None)

        raised = await raised_by(body())
        broken = executor.broken
    return raised, broken


# --------------------------------------------------------------------------- control calls
@dataclass(frozen=True)
class AbortedControl:
    error: BaseException | None
    injected: BaseException
    kinds: list[str]
    recorder: Recorder


async def control_aborted_at_start(env: ExecEnv, op: str, error: BaseException) -> AbortedControl:
    """Control `op` on a pin whose `query_started` observer raises `error`; what the driver logged
    during the call, and what a second observer saw."""
    recorder = Recorder()
    async with _executors().pin(ALIAS) as executor:
        with hooks().observe(Raising(STARTED, error)), hooks().observe(recorder):
            raised = await raised_by(call(executor, op))
        kinds = kinds_of(env.default)
    return AbortedControl(raised, error, kinds, recorder)


@dataclass(frozen=True)
class AfterControl:
    control_error: BaseException | None
    kinds: list[str]
    broken: bool
    """Whether a real cancellation of a held statement afterwards broke the pin."""


async def cancel_after_observed_control(
    env: ExecEnv, control: str, on: str, *, begin_first: bool
) -> AfterControl:
    """On a pin (after `begin()` when `begin_first`), `control` runs under an observer raising
    `CancelledError` on `on`; then a statement is held in the driver and its task really
    cancelled. The pin is broken only when a transaction was still open."""
    hold = env.default.hold_statements()
    seen: dict[str, Any] = {}

    async def worker() -> None:
        async with _executors().pin(ALIAS) as executor:
            seen["executor"] = executor
            if begin_first:
                await executor.begin()
            with hooks().observe(Raising(on, asyncio.CancelledError())):
                seen["error"] = await raised_by(getattr(executor, control)())
            seen["kinds"] = kinds_of(env.default)
            await executor.fetch_all("SELECT 1", None)

    task = asyncio.create_task(worker())
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return AfterControl(seen["error"], seen["kinds"], seen["executor"].broken)


async def rollback_after_aborted_commit(env: ExecEnv) -> tuple[BaseException | None, list[str]]:
    """`begin()`, a `commit()` stopped by its `query_started` observer, then `rollback()`:
    `(rollback error, driver log)`."""
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        with hooks().observe(Raising(STARTED, asyncio.CancelledError())):
            await raised_by(executor.commit())
        error = await raised_by(executor.rollback())
        kinds = kinds_of(env.default)
    return error, kinds


# --------------------------------------------------------------------------- several observers
async def two_observers_raising(
    on: str, first: BaseException, second: BaseException, op: str, *, pinned: bool
) -> BaseException | None:
    """Two observers raise different BaseExceptions on the same event of `op`."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(Raising(on, first)), hooks().observe(Raising(on, second)):
        return await raised_by(run())


@dataclass(frozen=True)
class Compared:
    plain: tuple[Any, Any]
    with_failing: tuple[Any, Any]
    """`(result, finished rowcount)` without, and with a failing observer in front."""
    error: BaseException | None


async def compare_with_failing_observer(failing: Any, op: str, *, pinned: bool) -> Compared:
    """`op` observed by a recorder alone, then again with `failing` registered before it."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    plain = Recorder()
    with hooks().observe(plain):
        plain_result = await run()
    other = Recorder()
    with hooks().observe(failing), hooks().observe(other):
        result, error = await _outcome(run())
    return Compared(
        (plain_result, plain.only_finished.rowcount),
        (result, other.only_finished.rowcount),
        error,
    )
