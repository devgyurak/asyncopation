"""Small runners for engine unit tests that need to look at an error (no `try` in tests)."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from asyncopation._django.connections import ScopeContextError
from asyncopation.compile.scope import compile_scope
from asyncopation.connection import registry
from asyncopation.connection.executor import pin
from asyncopation.engine.runner import Core, run_core
from tests.support import connection_entry
from tests.support.engine.cores import (
    autocommit_reading_core,
    exception_recording_core,
    finalizing_core,
    raising_finally_core,
    scope_logging_core,
    stubborn_core,
)
from tests.support.engine.evaluate import ALIAS, CompileEnv


async def run_expecting_error(core: Core[Any], alias: str) -> BaseException | None:
    """The exception `run_core` raised, or `None`."""
    try:
        await run_core(core, alias)
    except Exception as exc:
        return exc
    return None


@dataclass(frozen=True)
class CancelledRun:
    """A `run_core` cancelled while its statement was in flight."""

    cancelled: bool
    log: list[str]
    in_flight_after: int
    notes: tuple[str, ...] = ()


async def run_cancelled_in_flight(env: CompileEnv, query: Any) -> CancelledRun:
    """Hold the statement of a `finalizing_core`, cancel the run, report how it ended."""
    hold = env.default.hold_statements()
    log: list[str] = []
    core = finalizing_core(query, log)  # referenced here: garbage collection must not close it
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return CancelledRun(task.cancelled(), list(log), registry.in_flight(ALIAS))


async def exceptions_seen_by_a_cancelled_core(env: CompileEnv, query: Any) -> list[str]:
    """The exceptions that reach a core when its run is cancelled in flight: the core is closed
    (`GeneratorExit`), the `CancelledError` is not thrown into it."""
    hold = env.default.hold_statements()
    log: list[str] = []
    core = exception_recording_core(
        query, log
    )  # referenced here: garbage collection must not close it
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return log


async def run_finalizing_with_error(env: CompileEnv, query: Any) -> list[str]:
    """The close log of a `finalizing_core` whose statement failed (the core does not handle it)."""
    env.default.queue(RuntimeError("driver failed"))
    log: list[str] = []
    await run_expecting_error(finalizing_core(query, log), ALIAS)
    return log


async def run_finalizing(env: CompileEnv, query: Any) -> list[str]:
    """The close log of a `finalizing_core` that ran to its end."""
    env.default.queue([(1,)])
    log: list[str] = []
    await run_core(finalizing_core(query, log), ALIAS)
    return log


async def scope_seen_by_statements(env: CompileEnv, query: Any) -> list[bool]:
    """Whether a Django connection entry exists while each statement awaits: it must not (the
    compile scope never spans an `await`)."""
    seen: list[bool] = []

    async def peek() -> None:
        seen.append(connection_entry.has_entry(ALIAS))

    env.default.during = peek
    env.default.queue([(1,)])
    await run_core(finalizing_core(query, []), ALIAS)
    return seen


async def autocommit_seen_by_core(query: Any, *, pinned: bool) -> tuple[bool, bool]:
    """What `autocommit_reading_core` saw, with or without a pinned connection."""
    if pinned:
        async with pin(ALIAS):
            return await run_core(autocommit_reading_core(query, ALIAS), ALIAS)
    return await run_core(autocommit_reading_core(query, ALIAS), ALIAS)


async def scope_open_while_a_cancelled_core_closes(env: CompileEnv, query: Any) -> list[bool]:
    """Whether the compile scope is open when `run_core` closes a core suspended by a cancel."""
    hold = env.default.hold_statements()
    log: list[bool] = []
    core = scope_logging_core(
        query, ALIAS, log
    )  # referenced here: garbage collection must not close it
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return log


async def run_stubborn_cancelled_in_flight(env: CompileEnv, query: Any) -> CancelledRun:
    """Like `run_cancelled_in_flight` with a core that ignores `GeneratorExit` (`close()` then
    raises `RuntimeError`): the run must still end as cancelled."""
    hold = env.default.hold_statements()
    log: list[str] = []
    core = stubborn_core(query, log)  # referenced here: garbage collection must not close it
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    notes = await _cancellation_notes(task)
    return CancelledRun(task.cancelled(), list(log), registry.in_flight(ALIAS), notes)


async def run_cancelled_with_failing_cleanup(
    env: CompileEnv, query: Any, error: Exception
) -> CancelledRun:
    """Cancel a run whose core's `finally` raises `error`: the run must still end as cancelled,
    with the cleanup error attached to the `CancelledError` as a note."""
    hold = env.default.hold_statements()
    log: list[str] = []
    core = raising_finally_core(
        query, log, error
    )  # referenced: garbage collection must not close it
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    notes = await _cancellation_notes(task)
    return CancelledRun(task.cancelled(), list(log), registry.in_flight(ALIAS), notes)


async def _cancellation_notes(task: asyncio.Future[Any]) -> tuple[str, ...]:
    try:
        task.result()
    except asyncio.CancelledError as cancelled:
        return tuple(getattr(cancelled, "__notes__", ()))
    return ()


async def run_cancelled_with_scope_error(
    env: CompileEnv, query: Any, monkeypatch: Any
) -> BaseException | None:
    """Cancel a run whose scope fails on exit while the core is closed: the error it raises."""
    armed: list[str] = []

    @contextmanager
    def failing_scope(alias: str, wrapper: Any, *, autocommit: bool) -> Generator[None]:
        with compile_scope(alias, wrapper, autocommit=autocommit):
            yield
        if armed:
            raise ScopeContextError("exited from another Context")

    monkeypatch.setattr("asyncopation.engine.runner.compile_scope", failing_scope)
    hold = env.default.hold_statements()
    core = exception_recording_core(query, armed)  # the GeneratorExit record arms the failure
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return task.exception() if not task.cancelled() else None


class Interrupt(BaseException):
    """A `BaseException` that is not a cancellation: a nested interrupt of the close or a driver."""


class BrokenRepr(Exception):
    """An error whose `repr` fails, like a user exception with a faulty `__repr__`."""

    def __repr__(self) -> str:
        raise ValueError("repr failed")


async def run_cancelled_with_interrupting_cleanup(
    env: CompileEnv, query: Any
) -> BaseException | None:
    """Cancel a run whose core's `finally` raises an `Interrupt` while the core is closed: the
    exception the run ends with (`None` when it ends as cancelled)."""
    hold = env.default.hold_statements()
    core = raising_finally_core(query, [], Interrupt("interrupted"))  # referenced: no GC close
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return task.exception() if not task.cancelled() else None


async def run_failed_with_failing_cleanup(
    env: CompileEnv, query: Any, error: Exception
) -> BaseException | None:
    """A run whose statement fails with an `Interrupt` (not an `Exception`, so it is not thrown
    into the core) and whose core's `finally` raises `error` on close: what the run raises."""
    env.default.queue(Interrupt("driver interrupted"))
    core = raising_finally_core(query, [], error)
    try:
        await run_core(core, ALIAS)
    except Interrupt as interrupt:
        return interrupt
    return None


async def run_cancelled_with_scope_enter_error(
    env: CompileEnv, query: Any, monkeypatch: Any
) -> BaseException | None:
    """Cancel a run whose scope fails on enter when the core is closed: the error it raises."""
    armed: list[str] = []

    @contextmanager
    def failing_scope(alias: str, wrapper: Any, *, autocommit: bool) -> Generator[None]:
        if armed:
            raise ScopeContextError("cannot enter the scope")
        with compile_scope(alias, wrapper, autocommit=autocommit):
            yield

    monkeypatch.setattr("asyncopation.engine.runner.compile_scope", failing_scope)
    hold = env.default.hold_statements()
    core = finalizing_core(query, [])
    task = asyncio.ensure_future(run_core(core, ALIAS))
    await hold.wait_entered()
    armed.append("close")
    task.cancel()
    await asyncio.wait([task])
    return task.exception() if not task.cancelled() else None
