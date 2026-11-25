"""PostgreSQL scenarios for the executors: real pool, real backends, observed via pg_stat_activity.

Every function only *observes* and returns an outcome; the tests assert. Sessions of a test carry
its unique `application_name` (the `app_name` fixture), so the server side of a leak is countable.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from asyncopation.drivers.base import PoolStats
from tests.support.aio import wait_until
from tests.support.drivers.admin import admin_scalar, backend_ends, terminate_backend
from tests.support.executors.ops import in_gather_child, in_taskgroup_child, raised_by
from tests.support.lifecycle.postgres import server_connections_after_settling

ALIAS = "default"
HUNDRED = "SELECT g FROM generate_series(1, 100) g"
IN_TRANSACTION_SQL = (
    "SELECT xact_start < query_start FROM pg_stat_activity WHERE pid = pg_backend_pid()"
)


def _executors() -> Any:
    from asyncopation.connection import executor

    return executor


def _registry() -> Any:
    from asyncopation.connection import registry

    return registry


def _marker() -> str:
    return f"exec_{uuid.uuid4().hex}"


async def pid_of(executor: Any) -> int:
    return (await executor.fetch_all("SELECT pg_backend_pid()", None))[0][0]


async def in_open_transaction(executor: Any) -> bool:
    """True when the executor's connection is inside a transaction that ran an earlier statement."""
    return bool((await executor.fetch_all(IN_TRANSACTION_SQL, None))[0][0])


async def _active_like(app_name: str, pattern: str) -> bool:
    count = await admin_scalar(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE application_name = %s AND query LIKE %s AND pid <> pg_backend_pid() "
        "AND state = 'active'",
        (app_name, pattern),
    )
    return count > 0


async def settled_pool(within: float = 5.0) -> PoolStats:
    """Pool counters once nothing is in use or waiting (a discarded connection is replaced)."""

    async def idle() -> bool:
        stats = _registry().pool_stats(ALIAS)
        return stats.in_use == 0 and stats.waiting == 0

    with suppress(TimeoutError):
        await wait_until(idle, within=within)
    return _registry().pool_stats(ALIAS)


async def close_and_count_sessions(app_name: str) -> tuple[BaseException | None, int]:
    """`close()` (it waits for anything still in flight) and the server sessions left after it."""
    from asyncopation.connection import close

    error = await raised_by(close(timeout=3.0))
    return error, await server_connections_after_settling(app_name)


# --------------------------------------------------------------------------- concurrency
@dataclass(frozen=True)
class Gathered:
    backends: int


async def gather_sleeping_queries(count: int, seconds: float) -> Gathered:
    """`count` borrowed `pg_sleep(seconds)` queries gathered: how many backends served them."""
    executor = _executors().BorrowedExecutor(ALIAS)
    results = await asyncio.gather(
        *[
            executor.fetch_all(f"SELECT pg_backend_pid(), pg_sleep({seconds})", None)
            for _ in range(count)
        ]
    )
    return Gathered(len({rows[0][0] for rows in results}))


async def pinned_backends(statements: int) -> list[int]:
    """The backend pid seen by each of `statements` statements of one pin."""
    async with _executors().pin(ALIAS) as ex:
        return [await pid_of(ex) for _ in range(statements)]


@dataclass(frozen=True)
class CancelledPin:
    cancelled: bool
    in_flight: int
    pool: PoolStats
    broken: bool


async def cancel_pin_mid_statement(app_name: str) -> CancelledPin:
    """Cancel the owner of an autocommit pin while a slow statement runs on the server."""
    marker = _marker()
    seen: list[Any] = []

    async def worker() -> None:
        async with _executors().pin(ALIAS) as ex:
            seen.append(ex)
            await ex.fetch_all(f"SELECT pg_sleep(30) /* {marker} */", None)

    task = asyncio.create_task(worker())
    await wait_until(lambda: _active_like(app_name, f"%{marker}%"))
    task.cancel()
    await asyncio.wait([task])
    pool = await settled_pool()
    return CancelledPin(task.cancelled(), _registry().in_flight(ALIAS), pool, seen[0].broken)


# --------------------------------------------------------------------------- owner rule
@dataclass(frozen=True)
class ChildInPin:
    error: BaseException | None
    in_transaction_before: bool
    rollback_error: BaseException | None
    in_transaction_after: bool
    broken: bool
    pid_stable: bool


async def child_queries_inside_transaction(*, taskgroup: bool) -> ChildInPin:
    """A gather (or TaskGroup) child runs a query on the pin of an open transaction; then the owner
    rolls back."""
    run = in_taskgroup_child if taskgroup else in_gather_child
    async with _executors().pin(ALIAS) as ex:
        pid = await pid_of(ex)
        await ex.begin()
        await ex.fetch_all("SELECT 1", None)
        error = await raised_by(run(ex.fetch_all("SELECT 2", None)))
        inside = await in_open_transaction(ex)
        rollback_error = await raised_by(ex.rollback())
        after = await in_open_transaction(ex)
        return ChildInPin(error, inside, rollback_error, after, ex.broken, pid == await pid_of(ex))


async def savepoint_from_taskgroup_child() -> BaseException | None:
    async with _executors().pin(ALIAS) as ex:
        await ex.begin()
        error = await raised_by(in_taskgroup_child(ex.savepoint("sp")))
        await ex.rollback()
    return error


async def create_task_child_after_exit() -> BaseException | None:
    """A task created inside the block queries after the block exited."""
    release = asyncio.Event()

    async def late() -> None:
        await release.wait()
        await _executors().current_executor(ALIAS).fetch_all("SELECT 1", None)

    async with _executors().pin(ALIAS):
        task = asyncio.create_task(late())
    release.set()
    return await raised_by(task)


# --------------------------------------------------------------------------- broken transaction
@dataclass(frozen=True)
class BrokenByDeadline:
    timed_out: bool
    broken: bool
    next_error: BaseException | None
    rollback_error: BaseException | None
    backend_ended: bool
    pool: PoolStats
    in_flight: int


async def _slow_statement(executor: Any, *, stream: bool) -> None:
    """A statement that keeps the server busy: a plain query, or the first read of a stream."""
    if stream:
        async with executor.stream("SELECT pg_sleep(30)", None, 1) as chunks:
            await anext(chunks)
    else:
        await executor.fetch_all("SELECT pg_sleep(30)", None)


async def deadline_inside_transaction(*, stream: bool = False) -> BrokenByDeadline:
    """A statement (or the first read of a stream) of an open transaction hits a deadline."""
    timed_out = False
    async with _executors().pin(ALIAS) as ex:
        pid = await pid_of(ex)
        await ex.begin()
        try:
            async with asyncio.timeout(0.5):
                await _slow_statement(ex, stream=stream)
        except TimeoutError:
            timed_out = True
        broken = ex.broken
        next_error = await raised_by(ex.fetch_all("SELECT 1", None))
        rollback_error = await raised_by(ex.rollback())
    ended = await backend_ends(pid)
    pool = await settled_pool()
    return BrokenByDeadline(
        timed_out, broken, next_error, rollback_error, ended, pool, _registry().in_flight(ALIAS)
    )


async def deadline_outside_transaction() -> tuple[bool, BaseException | None, bool]:
    """The same deadline without `begin()`: (timed_out, error of the next statement, broken)."""
    timed_out = False
    async with _executors().pin(ALIAS) as ex:
        try:
            async with asyncio.timeout(0.5):
                await ex.fetch_all("SELECT pg_sleep(30)", None)
        except TimeoutError:
            timed_out = True
        next_error = await raised_by(ex.fetch_all("SELECT 1", None))
        return timed_out, next_error, ex.broken


# --------------------------------------------------------------------------- streams
@dataclass(frozen=True)
class PinnedStreamBreak:
    first_chunk: list[Any]
    read_after_block: BaseException | None
    read_after_pin: BaseException | None
    in_flight: int
    pool: PoolStats


async def break_out_of_a_pinned_stream() -> PinnedStreamBreak:
    """Read one chunk of a stream inside a transaction and leave it; read again later."""
    async with _executors().pin(ALIAS) as ex:
        await ex.begin()
        async with ex.stream(HUNDRED, None, 2) as chunks:
            first = await anext(chunks)
        after_block = await raised_by(anext(chunks))
        await ex.rollback()
    after_pin = await raised_by(anext(chunks))
    pool = await settled_pool()
    return PinnedStreamBreak(first, after_block, after_pin, _registry().in_flight(ALIAS), pool)


async def leave_pin_with_open_stream() -> tuple[BaseException | None, int, PoolStats]:
    """Leave the pin block while a stream is still open: (read error, in_flight, settled pool)."""
    pin_cm = _executors().pin(ALIAS)
    ex = await pin_cm.__aenter__()
    manager = ex.stream(HUNDRED, None, 2)
    chunks = await manager.__aenter__()
    await anext(chunks)
    await pin_cm.__aexit__(None, None, None)
    read_error = await raised_by(anext(chunks))
    await manager.__aexit__(None, None, None)
    pool = await settled_pool()
    return read_error, _registry().in_flight(ALIAS), pool


# --------------------------------------------------------------------------- pool waiters (6a)
@dataclass(frozen=True)
class Waiter:
    cancelled: bool
    in_flight_after_cancel: int
    pool: PoolStats
    in_flight: int
    fresh_rows: list[Any]


async def _hold_the_only_connection() -> tuple[Any, Any]:
    manager = _executors().BorrowedExecutor(ALIAS).stream(HUNDRED, None, 2)
    chunks = await manager.__aenter__()
    await anext(chunks)
    return manager, chunks


async def _one_waiter() -> bool:
    return _registry().pool_stats(ALIAS).waiting == 1


async def _settled_outcome(waiter: asyncio.Task[Any], in_flight_after_cancel: int) -> Waiter:
    pool = await settled_pool()
    fresh = await _executors().BorrowedExecutor(ALIAS).fetch_all("SELECT 1", None)
    return Waiter(
        waiter.cancelled(),
        in_flight_after_cancel,
        pool,
        _registry().in_flight(ALIAS),
        fresh,
    )


async def cancel_while_waiting_for_a_connection() -> Waiter:
    """The pool (size 1) is held by a stream; a waiting borrower is cancelled in `getconn`."""
    holder, _chunks = await _hold_the_only_connection()
    waiter = asyncio.create_task(_executors().BorrowedExecutor(ALIAS).fetch_all("SELECT 1", None))
    await wait_until(_one_waiter)
    waiter.cancel()
    await asyncio.wait([waiter])
    state = _registry().in_flight(ALIAS)
    await holder.__aexit__(None, None, None)
    return await _settled_outcome(waiter, state)


async def cancel_racing_the_release(delay: float, *, cancel_first: bool) -> Waiter:
    """Release the connection a borrower waits for and cancel that borrower at about the same
    moment (`delay` seconds apart, or the cancel first)."""
    holder, _chunks = await _hold_the_only_connection()
    waiter = asyncio.create_task(_executors().BorrowedExecutor(ALIAS).fetch_all("SELECT 1", None))
    await wait_until(_one_waiter)
    if cancel_first:
        waiter.cancel()
        await holder.__aexit__(None, None, None)
    else:
        await holder.__aexit__(None, None, None)
        await asyncio.sleep(delay)
        waiter.cancel()
    await asyncio.wait([waiter])
    return await _settled_outcome(waiter, _registry().in_flight(ALIAS))


# --------------------------------------------------------------------------- lost backends
@dataclass(frozen=True)
class TerminatedBackend:
    terminated: bool
    next_error: BaseException | None
    broken: bool
    rollback_error: BaseException | None
    exit_error: BaseException | None
    in_flight: int
    pool: PoolStats


async def terminate_pinned_backend() -> TerminatedBackend:
    """`pg_terminate_backend` of the backend of a pin inside a transaction: what the next
    statement, `rollback()` and the pin exit do."""
    seen: dict[str, Any] = {}

    async def block() -> None:
        async with _executors().pin(ALIAS) as ex:
            pid = await pid_of(ex)
            await ex.begin()
            seen["terminated"] = await terminate_backend(pid)
            seen["next_error"] = await raised_by(ex.fetch_all("SELECT 1", None))
            seen["broken"] = ex.broken
            seen["rollback_error"] = await raised_by(ex.rollback())

    exit_error = await raised_by(block())
    pool = await settled_pool()
    return TerminatedBackend(
        seen["terminated"],
        seen["next_error"],
        seen["broken"],
        seen["rollback_error"],
        exit_error,
        _registry().in_flight(ALIAS),
        pool,
    )


@dataclass(frozen=True)
class CloseUnderPin:
    close_error: BaseException | None
    statement_error: BaseException | None
    exit_error: BaseException | None
    backend_ended: bool
    in_flight: int
    pool: PoolStats


async def close_while_a_transaction_is_pinned(give_up_after: float) -> CloseUnderPin:
    """`close(timeout=give_up_after)` gives up on a pin whose transaction is open; then the owner runs a
    statement and leaves the block."""
    from asyncopation.connection import close

    pin_cm = _executors().pin(ALIAS)
    ex = await pin_cm.__aenter__()
    pid = await pid_of(ex)
    await ex.begin()
    close_error = await raised_by(close(timeout=give_up_after))
    statement_error = await raised_by(ex.fetch_all("SELECT 1", None))
    exit_error = await raised_by(pin_cm.__aexit__(None, None, None))
    ended = await backend_ends(pid)
    return CloseUnderPin(
        close_error,
        statement_error,
        exit_error,
        ended,
        _registry().in_flight(ALIAS),
        _registry().pool_stats(ALIAS),
    )
