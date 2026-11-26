"""Multi-step executor scenarios on the gated recording drivers.

Every function here only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from tests.support.aio import wait_until
from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import (
    call,
    in_gather_child,
    in_taskgroup_child,
    kinds_of,
    raised_by,
)

ALIAS = "default"
SELECT = "SELECT 1"


def executor_module() -> Any:
    """`asyncopation.connection.executor`, imported when a scenario needs it."""
    from asyncopation.connection import executor

    return executor


_executors = executor_module  # transitional alias: older support modules still import it


def _registry() -> Any:
    from asyncopation.connection import registry

    return registry


async def call_current(alias: str, op: str) -> Any:
    """`op` on whatever `current_executor(alias)` returns in the calling task's context."""
    return await call(_executors().current_executor(alias), op)


@asynccontextmanager
async def executor_of(pinned: bool) -> AsyncGenerator[Any]:
    """A pinned executor (`pin`) or a borrowed one, for scenarios that run both."""
    mod = _executors()
    if pinned:
        async with mod.pin(ALIAS) as pinned_executor:
            yield pinned_executor
    else:
        yield mod.BorrowedExecutor(ALIAS)


# --------------------------------------------------------------------------- owner / re-entrancy
@dataclass(frozen=True)
class Reentrancy:
    error: BaseException | None
    next_error: BaseException | None


async def reenter_during_statement(env: ExecEnv) -> Reentrancy:
    """The owner uses the executor again while its own statement is in flight (a hook inside the
    driver call runs in the same task)."""
    errors: list[BaseException | None] = []
    async with _executors().pin(ALIAS) as ex:

        async def inner() -> None:
            env.default.during = None  # a wrongly accepted call must not recurse
            errors.append(await raised_by(ex.fetch_all("SELECT 2", None)))

        env.default.during = inner
        await ex.fetch_all(SELECT, None)
        next_error = await raised_by(ex.fetch_all("SELECT 3", None))
    return Reentrancy(errors[0], next_error)


async def reenter_during_stream(env: ExecEnv) -> Reentrancy:
    """The owner runs a statement while its own stream is open, then once the stream is closed."""
    async with _executors().pin(ALIAS) as ex:
        async with ex.stream(SELECT, None, 1) as chunks:
            await anext(chunks)
            error = await raised_by(ex.fetch_all("SELECT 2", None))
        next_error = await raised_by(ex.fetch_all("SELECT 3", None))
    return Reentrancy(error, next_error)


async def use_after_block_exit() -> Any:
    """A task created inside the block (it copied the context) uses the executor after the block
    exited; returns what `fetch_all` gave (raises when the executor refuses)."""
    release = asyncio.Event()

    async def late() -> Any:
        await release.wait()
        return await _executors().current_executor(ALIAS).fetch_all(SELECT, None)

    async with _executors().pin(ALIAS):
        task = asyncio.create_task(late())
    release.set()
    return await task


async def owner_use_after_block_exit(op: str) -> Any:
    """The owner keeps the executor it was given and uses it after the block."""
    async with _executors().pin(ALIAS) as ex:
        pass
    return await call(ex, op)


async def three_error_messages(env: ExecEnv) -> list[str]:
    """Messages of: non-owner use, re-entrant use, use after the block (they must differ)."""
    async with _executors().pin(ALIAS) as ex:
        other_task = await raised_by(in_gather_child(ex.fetch_all(SELECT, None)))
    re_entrant = (await reenter_during_statement(env)).error
    after_exit = await raised_by(use_after_block_exit())
    return [str(other_task), str(re_entrant), str(after_exit)]


async def kinds_after_owner_call(env: ExecEnv, op: str) -> list[str]:
    async with _executors().pin(ALIAS) as ex:
        await call(ex, op)
        return kinds_of(env.default)


# --------------------------------------------------------------------------- broken transactions
@dataclass(frozen=True)
class CancelOutcome:
    cancelled: BaseException | None
    broken: bool
    statement_error: BaseException | None
    rollback_error: BaseException | None
    kinds_inside: list[str]
    kinds_after_exit: list[str]
    closed_after_exit: bool


async def cancel_in_pin(env: ExecEnv, *, begin: bool, op: str = "fetch_all") -> CancelOutcome:
    """A statement of a pin is cancelled (the driver raises `CancelledError`), with or without a
    transaction; then a statement and `rollback()` are tried on the same executor."""
    driver = env.default
    async with _executors().pin(ALIAS) as ex:
        if begin:
            await ex.begin()
        driver.queue(asyncio.CancelledError())
        cancelled = await raised_by(call(ex, op))
        broken = ex.broken
        statement_error = await raised_by(ex.fetch_all("SELECT 2", None))
        rollback_error = await raised_by(ex.rollback())
        inside = kinds_of(driver)
    return CancelOutcome(
        cancelled, broken, statement_error, rollback_error, inside, kinds_of(driver), ex.closed
    )


@asynccontextmanager
async def broken_pin(env: ExecEnv) -> AsyncGenerator[Any]:
    """A pinned executor whose transaction is broken by a cancelled statement."""
    async with _executors().pin(ALIAS) as ex:
        await ex.begin()
        env.default.queue(asyncio.CancelledError())
        await raised_by(ex.fetch_all(SELECT, None))
        yield ex


@asynccontextmanager
async def lost_connection_pin(env: ExecEnv) -> AsyncGenerator[Any]:
    """A pinned executor whose transaction is broken by a statement that lost the connection."""
    from tests.support.executors.fakes import LostConnection

    env.default.disconnect_errors = (LostConnection,)
    async with _executors().pin(ALIAS) as ex:
        await ex.begin()
        env.default.queue(LostConnection())
        await raised_by(ex.fetch_all(SELECT, None))
        yield ex


async def second_pin_after_broken_exit(env: ExecEnv) -> Any:
    """Leave a broken pin, then pin the alias again and run a statement."""
    async with broken_pin(env):
        pass
    async with _executors().pin(ALIAS) as ex:
        return ex.broken, await ex.fetch_all(SELECT, None)


@dataclass(frozen=True)
class RealCancel:
    cancelled: bool
    broken: bool
    in_flight: int


async def cancel_owner_mid_statement(env: ExecEnv, *, begin: bool) -> RealCancel:
    """The task that owns a pin is cancelled while its statement is held in the driver."""
    hold = env.default.hold_statements()
    seen: list[Any] = []

    async def worker() -> None:
        async with _executors().pin(ALIAS) as ex:
            seen.append(ex)
            if begin:
                await ex.begin()
            await ex.fetch_all(SELECT, None)

    task = asyncio.create_task(worker())
    await hold.wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return RealCancel(task.cancelled(), seen[0].broken, _registry().in_flight(ALIAS))


# --------------------------------------------------------------------------- nesting
async def depth_trace() -> list[int]:
    trace: list[int] = []
    async with _executors().pin(ALIAS) as outer:
        trace.append(outer.depth)
        async with _executors().pin(ALIAS) as inner:
            trace.append(inner.depth)
        trace.append(outer.depth)
    return trace


@dataclass(frozen=True)
class Nested:
    same_executor: bool
    closed_after_inner_exit: bool
    closed_after_outer_exit: bool
    rows_after_inner_exit: list[Any]


async def nest_pins() -> Nested:
    mod = _executors()
    async with mod.pin(ALIAS) as outer:
        async with mod.pin(ALIAS) as inner:
            same = inner is outer
        closed_inner = outer.closed
        rows = await outer.fetch_all(SELECT, None)
    return Nested(same, closed_inner, outer.closed, rows)


async def borrows_in_nested_pins(env: ExecEnv) -> int:
    mod = _executors()
    async with mod.pin(ALIAS) as outer:
        await outer.fetch_all(SELECT, None)
        async with mod.pin(ALIAS) as inner:
            await inner.fetch_all(SELECT, None)
        await outer.fetch_all(SELECT, None)
    return env.default.borrows


async def borrows_in_pin(env: ExecEnv, statements: int) -> int:
    async with _executors().pin(ALIAS) as ex:
        for _ in range(statements):
            await ex.fetch_all(SELECT, None)
    return env.default.borrows


async def borrows_without_pin(env: ExecEnv, statements: int) -> int:
    ex = _executors().BorrowedExecutor(ALIAS)
    for _ in range(statements):
        await ex.fetch_all(SELECT, None)
    return env.default.borrows


async def repin_in_gather_child() -> None:
    mod = _executors()

    async def enter() -> None:
        async with mod.pin(ALIAS):
            pass

    async with mod.pin(ALIAS):
        await in_gather_child(enter())


@dataclass(frozen=True)
class TwoAliases:
    distinct: bool
    depths: tuple[int, int]
    default_kinds: list[str]
    other_kinds: list[str]


async def pin_two_aliases(env: ExecEnv) -> TwoAliases:
    mod = _executors()
    async with mod.pin("default") as first, mod.pin("other") as second:
        await first.fetch_all(SELECT, None)
        await second.execute("UPDATE t SET x = 1", None)
        return TwoAliases(
            first is not second,
            (first.depth, second.depth),
            kinds_of(env.default),
            kinds_of(env.other),
        )


async def other_alias_in_child_of_pin(env: ExecEnv) -> tuple[bool, list[Any], list[str]]:
    """While `default` is pinned, a child task uses `other`: borrowed, and it works."""

    async def use_other() -> tuple[bool, list[Any]]:
        executor = _executors().current_executor("other")
        return executor.pinned, await executor.fetch_all(SELECT, None)

    async with _executors().pin("default"):
        pinned, rows = await in_gather_child(use_other())
    return pinned, rows, kinds_of(env.other)


async def pin_other_alias_in_child() -> int:
    """A child task of a `default` pin opens its own pin on `other` (ADR-0001 Decision 4)."""

    async def pin_other() -> int:
        async with _executors().pin("other") as ex:
            return ex.depth

    async with _executors().pin("default"):
        return await in_gather_child(pin_other())


# --------------------------------------------------------------------------- close (D3)
@dataclass(frozen=True)
class FlightTrace:
    before: int
    idle: int
    inside_statement: int
    after_statement: int
    after: int
    at_borrow: list[int]


async def trace_pin_in_flight(env: ExecEnv) -> FlightTrace:
    reg = _registry()

    async def noop() -> None:
        return None

    env.default.during = noop
    before = reg.in_flight(ALIAS)
    async with _executors().pin(ALIAS) as ex:
        idle = reg.in_flight(ALIAS)
        await ex.fetch_all(SELECT, None)
        inside = env.default.during_in_flight[-1]
        after_statement = reg.in_flight(ALIAS)
    return FlightTrace(
        before, idle, inside, after_statement, reg.in_flight(ALIAS), env.default.in_flight_at_borrow
    )


async def trace_borrowed_in_flight(env: ExecEnv) -> FlightTrace:
    reg = _registry()

    async def noop() -> None:
        return None

    env.default.during = noop
    before = reg.in_flight(ALIAS)
    await _executors().BorrowedExecutor(ALIAS).fetch_all(SELECT, None)
    inside = env.default.during_in_flight[-1]
    return FlightTrace(
        before, before, inside, reg.in_flight(ALIAS), reg.in_flight(ALIAS),
        env.default.in_flight_at_borrow,
    )  # fmt: skip


@dataclass(frozen=True)
class Failed:
    error: BaseException | None
    in_flight: int
    released: list[bool]


async def failing_borrowed_statement(env: ExecEnv, op: str, error: BaseException) -> Failed:
    """`op` on a borrowed executor, the driver raising `error` (queued) for it."""
    env.default.queue(error)
    raised = await raised_by(call(_executors().BorrowedExecutor(ALIAS), op))
    return Failed(raised, _registry().in_flight(ALIAS), env.default.released)


async def borrow_from_unknown_alias() -> Failed:
    raised = await raised_by(call(_executors().BorrowedExecutor("missing"), "fetch_all"))
    return Failed(raised, _registry().in_flight("missing"), [])


async def cancel_borrowed_mid_statement(env: ExecEnv) -> tuple[bool, int, int]:
    """A task is cancelled while its borrowed statement is held: (cancelled, in_flight_then,
    in_flight_after)."""
    hold = env.default.hold_statements()
    task = asyncio.create_task(_executors().BorrowedExecutor(ALIAS).fetch_all(SELECT, None))
    await hold.wait_entered()
    during = _registry().in_flight(ALIAS)
    task.cancel()
    await asyncio.wait([task])
    return task.cancelled(), during, _registry().in_flight(ALIAS)


@dataclass(frozen=True)
class Concurrent:
    peak: int
    borrows: int
    after: int
    released: list[bool]


async def concurrent_borrowed(env: ExecEnv, count: int) -> Concurrent:
    hold = env.default.hold_statements()
    executor = _executors().BorrowedExecutor(ALIAS)
    tasks = [asyncio.create_task(executor.fetch_all(SELECT, None)) for _ in range(count)]
    await hold.wait_entered(count)
    peak = _registry().in_flight(ALIAS)
    hold.release()
    await asyncio.gather(*tasks)
    return Concurrent(peak, env.default.borrows, _registry().in_flight(ALIAS), env.default.released)


async def _is_closing() -> bool:
    return _registry().is_closing(asyncio.get_running_loop())


@asynccontextmanager
async def _open_unit(kind: str) -> AsyncGenerator[Any]:
    """What `close()` must wait for: an open pin block or an open stream."""
    mod = _executors()
    if kind == "pin":
        async with mod.pin(ALIAS) as ex:
            yield ex
    else:
        async with mod.BorrowedExecutor(ALIAS).stream(SELECT, None, 1) as chunks:
            yield chunks


@dataclass(frozen=True)
class CloseWait:
    done_while_open: bool
    in_flight_while_open: int
    error: BaseException | None
    driver_closed: bool
    exit_error: BaseException | None
    in_flight_after: int


async def close_while_open(
    env: ExecEnv, kind: str, close_timeout: float, *, give_up: bool
) -> CloseWait:
    """`close(timeout)` while a pin block or a stream is open; the unit is left afterwards.

    `give_up`: wait for the close to time out first; otherwise leave the unit first."""
    from asyncopation.connection import close

    reg = _registry()
    cm = _open_unit(kind)
    await cm.__aenter__()
    task = asyncio.create_task(close(timeout=close_timeout))
    await wait_until(_is_closing)  # close() runs until it blocks on the open unit, then we look
    done_while_open, in_flight = task.done(), reg.in_flight(ALIAS)
    if give_up:
        error = await raised_by(task)
        closed = env.default.closed
        exit_error = await raised_by(cm.__aexit__(None, None, None))
    else:
        exit_error = await raised_by(cm.__aexit__(None, None, None))
        error = await raised_by(task)
        closed = env.default.closed
    return CloseWait(done_while_open, in_flight, error, closed, exit_error, reg.in_flight(ALIAS))


# --------------------------------------------------------------------------- current_executor
async def current_in_child_after_exit() -> Any:
    """`current_executor` seen by a task created inside a pin block, after the block exited."""
    release = asyncio.Event()

    async def late() -> Any:
        await release.wait()
        return _executors().current_executor(ALIAS)

    async with _executors().pin(ALIAS):
        task = asyncio.create_task(late())
    release.set()
    return await task


async def current_in_gather_child() -> tuple[Any, Any]:
    async def look() -> Any:
        return _executors().current_executor(ALIAS)

    async with _executors().pin(ALIAS) as ex:
        return ex, await in_gather_child(look())


async def current_after_block() -> Any:
    async with _executors().pin(ALIAS):
        pass
    return _executors().current_executor(ALIAS)


# --------------------------------------------------------------------------- streams
@dataclass(frozen=True)
class StreamUse:
    in_flight_open: int
    released_open: list[bool]
    in_flight_after: int
    released_after: list[bool]
    streams_open_after: int


async def borrowed_stream_use(env: ExecEnv, *, leave_with: str) -> StreamUse:
    """Open a borrowed stream, read one chunk, leave it (`ok`, `error`, or `cancel`)."""
    reg = _registry()
    driver = env.default
    driver.queue([(1,), (2,), (3,)])
    state: dict[str, Any] = {}
    ready = asyncio.Event()

    async def use() -> None:
        async with _executors().BorrowedExecutor(ALIAS).stream(SELECT, None, 1) as chunks:
            await anext(chunks)
            state["open"] = (reg.in_flight(ALIAS), driver.released)
            if leave_with == "error":
                raise ValueError("boom")
            if leave_with == "cancel":
                ready.set()
                await asyncio.Event().wait()

    if leave_with == "cancel":
        task = asyncio.create_task(use())
        await ready.wait()
        task.cancel()
        await asyncio.wait([task])
    else:
        await raised_by(use())
    return StreamUse(*state["open"], reg.in_flight(ALIAS), driver.released, driver.streams_open)


@dataclass(frozen=True)
class PinnedStream:
    in_flight_open: int
    in_flight_after: int
    statement_after: list[Any]


async def stream_in_pin(env: ExecEnv) -> PinnedStream:
    reg = _registry()
    env.default.queue([(1,), (2,)])
    async with _executors().pin(ALIAS) as ex:
        async with ex.stream(SELECT, None, 1) as chunks:
            await anext(chunks)
            during = reg.in_flight(ALIAS)
        after = reg.in_flight(ALIAS)
        rows = await ex.fetch_all(SELECT, None)
    return PinnedStream(during, after, rows)


@dataclass(frozen=True)
class ForeignClose:
    error: BaseException | None
    in_flight_after: int
    streams_open: int


async def close_stream_from_child(env: ExecEnv, *, pinned: bool) -> ForeignClose:
    """Close a stream in a task that is not the one that opened it (an async-generator finalizer)."""
    reg = _registry()
    env.default.queue([(1,), (2,)])
    async with executor_of(pinned) as ex:
        manager = ex.stream(SELECT, None, 1)
        chunks = await manager.__aenter__()
        await anext(chunks)
        error = await raised_by(in_gather_child(manager.__aexit__(None, None, None)))
    return ForeignClose(error, reg.in_flight(ALIAS), env.default.streams_open)


@dataclass(frozen=True)
class StreamAfterPin:
    streams_open: int
    exit_error: BaseException | None
    read_error: BaseException | None
    late_close_error: BaseException | None
    in_flight_after: int


async def stream_left_open_at_pin_exit(env: ExecEnv) -> StreamAfterPin:
    """Leave the pin block while a stream is still open, then read and close that stream."""
    env.default.queue([(1,), (2,), (3,)])
    pin_cm = _executors().pin(ALIAS)
    ex = await pin_cm.__aenter__()
    manager = ex.stream(SELECT, None, 1)
    chunks = await manager.__aenter__()
    await anext(chunks)
    exit_error = await raised_by(pin_cm.__aexit__(None, None, None))
    streams_open = env.default.streams_open
    read_error = await raised_by(anext(chunks))
    late = await raised_by(manager.__aexit__(None, None, None))
    return StreamAfterPin(streams_open, exit_error, read_error, late, _registry().in_flight(ALIAS))


async def read_chunk_in_child(env: ExecEnv) -> Any:
    """Read the next chunk of a pinned stream from a task that is not the owner."""
    env.default.queue([(1,), (2,), (3,)])
    async with _executors().pin(ALIAS) as ex, ex.stream(SELECT, None, 1) as chunks:
        await anext(chunks)
        return await in_gather_child(anext(chunks))


async def savepoint_in_taskgroup_child() -> Any:
    async with _executors().pin(ALIAS) as ex:
        return await in_taskgroup_child(ex.savepoint("sp"))


async def _enter_and_leave_pin() -> None:
    async with _executors().pin(ALIAS):
        pass


async def use_while_closing(kind: str) -> Failed:
    """Borrow (or pin) while a `close()` is pending: refused, and the counter does not leak."""
    attempt = (
        _enter_and_leave_pin()
        if kind == "pin"
        else call(_executors().BorrowedExecutor(ALIAS), "fetch_all")
    )
    raised = await raised_by(attempt)
    return Failed(raised, _registry().in_flight(ALIAS), [])
