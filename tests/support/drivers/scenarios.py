"""Multi-step scenarios against a driver: cancellation, kills, timeouts, open failures.

Every function here only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any
from unittest import mock

import psycopg
import psycopg_pool

from asyncopation.drivers.base import AsyncDbConnection, PoolStats
from tests.support.aio import wait_until
from tests.support.drivers.admin import (
    UTC_CASE,
    Row,
    TzCase,
    admin_scalar,
    backend_ends,
    build_driver,
    login_overrides,
    open_driver,
    scratch_count,
    set_role_login,
    set_role_password,
    settled_stats,
    temporary_login_role,
    terminate_backend,
    timed,
)
from tests.support.drivers.probes import (
    CursorRow,
    backend_pid,
    error_of_operation,
    in_transaction,
    insert_named,
    open_cursors,
    reuse_error,
    session_setting,
)
from tests.support.drivers.reference import sync_connect_error

SLEEP_SECONDS = 30
HUNDRED = "SELECT g FROM generate_series(1, 100) g"
SLEEPY_FETCH = f"SELECT g, pg_sleep({SLEEP_SECONDS}) FROM generate_series(1, 100) g"


# --------------------------------------------------------------------------- probes on activity
async def _active_like(pattern: str) -> bool:
    count = await admin_scalar(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE query LIKE %s AND pid <> pg_backend_pid() AND state = 'active'",
        (pattern,),
    )
    return count > 0


def _marker() -> str:
    return f"probe_{uuid.uuid4().hex}"


def _event_probe(event: asyncio.Event) -> Callable[[], Awaitable[bool]]:
    async def probe() -> bool:
        return event.is_set()

    return probe


def _activity_probe(pattern: str) -> Callable[[], Awaitable[bool]]:
    async def probe() -> bool:
        return await _active_like(pattern)

    return probe


# --------------------------------------------------------------------------- cancel + inspect
@dataclass(frozen=True)
class Aftermath:
    """What is left after a task was cancelled while it held a connection."""

    cancelled: bool
    reuse_error: BaseException | None
    old_backend_ended: bool
    fresh_cursors: list[CursorRow]
    after: PoolStats
    old_pid: int
    fresh_pid: int
    rows_left: int | None


async def cancel_and_inspect(
    driver: Any,
    worker: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    started: Callable[[], Awaitable[bool]],
    *,
    around: AbstractContextManager[Any] | None = None,
    table: str | None = None,
) -> Aftermath:
    """Run `worker`, cancel it once `started()` is true, then inspect old and new connection."""
    held: dict[str, Any] = {}
    with around or contextlib.nullcontext():
        task = asyncio.create_task(worker(held))
        await wait_until(started)
        task.cancel()
        await asyncio.wait([task])
    error = await reuse_error(held["conn"])
    ended = await backend_ends(held["pid"])
    after = await settled_stats(driver)
    async with driver.connection() as fresh:
        cursors = await open_cursors(fresh)
        fresh_pid = await backend_pid(fresh)
    rows_left = await scratch_count(table) if table else None
    return Aftermath(
        task.cancelled(), error, ended, cursors, after, held["pid"], fresh_pid, rows_left
    )


async def cancel_open_stream(driver: Any, *, transactional: bool) -> Aftermath:
    """Cancel a task that sits inside an open stream (after its first chunk)."""
    ready = asyncio.Event()

    async def worker(held: dict[str, Any]) -> None:
        async with driver.connection() as conn:
            held["conn"], held["pid"] = conn, await backend_pid(conn)
            if transactional:
                await conn.begin()
            async with conn.stream(HUNDRED, None, 2) as chunks:
                await anext(chunks)
                ready.set()
                await asyncio.Event().wait()

    return await cancel_and_inspect(driver, worker, _event_probe(ready))


async def cancel_stream_fetch(driver: Any) -> Aftermath:
    """Cancel while a `FETCH` of a slow stream is in flight (inside a transaction)."""

    async def worker(held: dict[str, Any]) -> None:
        async with driver.connection() as conn:
            held["conn"], held["pid"] = conn, await backend_pid(conn)
            await conn.begin()
            async with conn.stream(SLEEPY_FETCH, None, 2) as chunks:
                await anext(chunks)

    return await cancel_and_inspect(driver, worker, _activity_probe("FETCH%"))


async def cancel_write_in_transaction(driver: Any, table: str) -> Aftermath:
    """Insert inside a transaction, then cancel while a later statement sleeps."""
    marker = _marker()

    async def worker(held: dict[str, Any]) -> None:
        async with driver.connection() as conn:
            held["conn"], held["pid"] = conn, await backend_pid(conn)
            await conn.begin()
            await insert_named(conn, table, "a")
            await conn.fetch_all(f"SELECT pg_sleep({SLEEP_SECONDS}) /* {marker} */", None)

    return await cancel_and_inspect(driver, worker, _activity_probe(f"%{marker}%"), table=table)


async def cancel_commit(driver: Any, table: str) -> Aftermath:
    """Cancel while `COMMIT` runs a slow deferred trigger (see `install_slow_commit`)."""

    async def worker(held: dict[str, Any]) -> None:
        async with driver.connection() as conn:
            held["conn"], held["pid"] = conn, await backend_pid(conn)
            await conn.begin()
            await insert_named(conn, table, "a")
            await conn.commit()

    return await cancel_and_inspect(driver, worker, _activity_probe("COMMIT%"), table=table)


def slow_cursor_close(entered: asyncio.Event) -> AbstractContextManager[Any]:
    """Patch psycopg's server cursor `close` so that it announces itself and then sleeps."""

    async def slow_close(self: Any) -> None:
        entered.set()
        try:
            await asyncio.sleep(3600)
        finally:
            self._closed = (
                True  # the backend is discarded; silence the "deleted while open" warning
            )

    return mock.patch.object(psycopg.AsyncServerCursor, "close", slow_close)


async def cancel_stream_close(driver: Any) -> Aftermath:
    """Cancel while the stream's cursor `CLOSE` is in flight (autocommit: a held cursor)."""
    entered = asyncio.Event()

    async def worker(held: dict[str, Any]) -> None:
        async with driver.connection() as conn:
            held["conn"], held["pid"] = conn, await backend_pid(conn)
            async with conn.stream(HUNDRED, None, 2) as chunks:
                await anext(chunks)

    return await cancel_and_inspect(
        driver, worker, _event_probe(entered), around=slow_cursor_close(entered)
    )


# --------------------------------------------------------------------------- autocommit cancel
@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool
    before: PoolStats
    after: PoolStats
    next_rows: list[Row]
    next_in_transaction: bool
    statement_stopped: bool
    pid_before: int
    pid_after: int


async def cancel_during_sleep(
    driver: Any, *, transactional: bool = False, operation: str = "fetch_all"
) -> CancelOutcome:
    """Cancel a task that sits in `pg_sleep(30)` (via `operation`) and describe the aftermath."""
    marker = _marker()
    sql = f"SELECT pg_sleep({SLEEP_SECONDS}) /* {marker} */ WHERE %s = 1"
    held: dict[str, int] = {}
    before = driver.pool_stats()

    async def worker() -> None:
        async with driver.connection() as conn:
            held["pid"] = await backend_pid(conn)
            if transactional:
                await conn.begin()
            if operation == "execute_many":
                await conn.execute_many(sql, [(1,)])
            else:
                await conn.fetch_all(sql, (1,))

    task = asyncio.create_task(worker())
    await wait_until(_activity_probe(f"%{marker}%"))
    task.cancel()
    await asyncio.wait([task])
    after = driver.pool_stats()

    async def stopped() -> bool:
        return not await _active_like(f"%{marker}%")

    stopped_in_time = True
    try:
        await wait_until(stopped, within=5.0)
    except TimeoutError:
        stopped_in_time = False
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT 1", None)
        in_txn = await in_transaction(conn)
        pid_after = await backend_pid(conn)
    return CancelOutcome(
        task.cancelled(), before, after, rows, in_txn, stopped_in_time, held["pid"], pid_after
    )


async def cancel_after_own_backend_killed(driver: Any) -> bool:
    """Cancel a task whose connection died inside a transaction; True when it ended cancelled."""
    ready = asyncio.Event()

    async def worker() -> None:
        async with driver.connection() as conn:
            await conn.begin()
            await terminate_backend(await backend_pid(conn))
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await wait_until(_event_probe(ready))
    task.cancel()
    await asyncio.wait([task])
    return task.cancelled()


# --------------------------------------------------------------------------- pool saturation
@dataclass(frozen=True)
class WaiterOutcome:
    cancelled: bool
    after: PoolStats
    reusable_rows: list[Row]


async def cancel_waiting_borrower(driver: Any) -> WaiterOutcome:
    """Saturate a one-connection pool, cancel a borrower that waits, release, inspect the pool."""

    async def waiter() -> None:
        async with driver.connection():
            return

    async def queued() -> bool:
        return driver.pool_stats().waiting == 1

    async with driver.connection():
        task = asyncio.create_task(waiter())
        await wait_until(queued)
        task.cancel()
        await asyncio.wait([task])
    after = driver.pool_stats()
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT 1", None)
    return WaiterOutcome(task.cancelled(), after, rows)


@dataclass(frozen=True)
class SameTickOutcome:
    cancelled: bool
    holder_pid: int
    next_pid: int
    after: PoolStats


async def release_and_cancel_waiter_together(driver: Any) -> SameTickOutcome:
    """ADR-0001 6a: the holder releases in the same loop tick in which the waiter is cancelled."""

    async def waiter() -> None:
        async with driver.connection():
            return

    async def queued() -> bool:
        return driver.pool_stats().waiting == 1

    async with driver.connection() as holder:
        holder_pid = await backend_pid(holder)
        task = asyncio.create_task(waiter())
        await wait_until(queued)
        task.cancel()
    await asyncio.wait([task])
    after = await settled_stats(driver)
    async with driver.connection() as conn:
        next_pid = await backend_pid(conn)
    return SameTickOutcome(task.cancelled(), holder_pid, next_pid, after)


async def concurrent_backend_pids(driver: Any, count: int = 2) -> list[int]:
    """Backend pids of `count` borrowers that all hold their connection at the same time."""
    barrier = asyncio.Barrier(count)

    async def borrow() -> int:
        async with driver.connection() as conn:
            pid = await backend_pid(conn)
            await barrier.wait()
            return pid

    async with asyncio.timeout(10):
        return list(await asyncio.gather(*(borrow() for _ in range(count))))


@dataclass(frozen=True)
class TimeoutOutcome:
    error: BaseException | None
    after: PoolStats


async def acquire_while_saturated(driver: Any) -> TimeoutOutcome:
    """A one-connection pool with a short timeout: the second borrower must time out."""
    error: BaseException | None = None
    async with driver.connection():
        try:
            async with driver.connection():
                pass
        except Exception as exc:
            error = exc
    return TimeoutOutcome(error, await settled_stats(driver))


def is_pool_timeout(exc: BaseException | None) -> bool:
    return exc is not None and isinstance(exc.__cause__, psycopg_pool.PoolTimeout)


# --------------------------------------------------------------------------- dead connections
@dataclass(frozen=True)
class KillOutcome:
    error: BaseException | None
    killed_pid: int
    after: PoolStats
    fresh_pid: int
    fresh_rows: list[Row]


async def _use_dead_connection(
    driver: Any, kill: Callable[[int], Awaitable[bool]], *, transactional: bool
) -> KillOutcome:
    error: BaseException | None = None
    pid = 0
    try:
        async with driver.connection() as conn:
            if transactional:
                await conn.begin()
                await conn.fetch_all("SELECT 1", None)
            pid = await backend_pid(conn)
            await kill(pid)
            await conn.fetch_all("SELECT 1", None)
    except Exception as exc:
        error = exc
    after = await settled_stats(driver, min_idle=1)  # the pool replaces the discarded connection
    async with driver.connection() as fresh:
        fresh_pid = await backend_pid(fresh)
        fresh_rows = await fresh.fetch_all("SELECT 1", None)
    return KillOutcome(error, pid, after, fresh_pid, fresh_rows)


async def kill_backend_then_query(driver: Any, *, transactional: bool = False) -> KillOutcome:
    """Terminate the backend of a borrowed connection, use it, then borrow again."""
    return await _use_dead_connection(driver, terminate_backend, transactional=transactional)


async def idle_timeout_then_query(driver: Any) -> KillOutcome:
    """The server ends an idle-in-transaction session (FATAL); the connection is used after."""
    return await _use_dead_connection(driver, backend_ends, transactional=True)


# --------------------------------------------------------------------------- raw transactions
async def raw_begin_insert_exit(driver: Any, table: str) -> tuple[list[Row], bool]:
    """`BEGIN` via plain SQL, insert, release normally; what the next borrower sees."""
    async with driver.connection() as first:
        await first.execute("BEGIN", None)
        await insert_named(first, table, "a")
    async with driver.connection() as second:
        rows = await second.fetch_all(f"SELECT name FROM {table}", None)
        inside = await in_transaction(second)
    return rows, inside


async def savepoint_after_integrity_error(
    conn: AsyncDbConnection, table: str
) -> BaseException | None:
    """Duplicate insert under a savepoint, roll back to it, keep working, commit."""
    error: BaseException | None = None
    await conn.begin()
    await insert_named(conn, table, "a")
    await conn.savepoint("sp_1")
    try:
        await insert_named(conn, table, "a")
    except Exception as exc:
        error = exc
    await conn.rollback_to_savepoint("sp_1")
    await insert_named(conn, table, "b")
    await conn.commit()
    return error


@dataclass(frozen=True)
class FailedTransaction:
    error: BaseException | None
    rows_after: list[Row]
    still_in_transaction: bool


async def fail_in_transaction_then_rollback(conn: AsyncDbConnection) -> FailedTransaction:
    error: BaseException | None = None
    await conn.begin()
    try:
        await conn.fetch_all("SELECT 1/0", None)
    except Exception as exc:
        error = exc
    await conn.rollback()
    rows = await conn.fetch_all("SELECT 1", None)
    return FailedTransaction(error, rows, await in_transaction(conn))


async def isolation_in_transaction(options: dict[str, Any]) -> str:
    async with open_driver(options=options) as driver, driver.connection() as conn:
        await conn.begin()
        level = await session_setting(conn, "transaction_isolation")
        await conn.rollback()
    return level


# --------------------------------------------------------------------------- misuse and errors
async def released_connection_error(driver: Any, operation: str) -> BaseException | None:
    """The exception raised by `operation` on a connection that was already released."""
    async with driver.connection() as conn:
        pass
    return await error_of_operation(conn, operation)


@dataclass(frozen=True)
class BodyErrorOutcome:
    error: BaseException | None
    pid_before: int
    pid_after: int
    after: PoolStats


async def raw_error_in_stream_body(driver: Any) -> BodyErrorOutcome:
    """The caller's own (raw psycopg) exception raised inside `async with conn.stream(...)`."""
    error: BaseException | None = None
    async with driver.connection() as conn:
        pid_before = await backend_pid(conn)
        try:
            async with conn.stream(HUNDRED, None, 2) as chunks:
                await anext(chunks)
                raise psycopg.OperationalError("x")
        except psycopg.OperationalError as exc:
            error = exc
    after = await settled_stats(driver)
    async with driver.connection() as conn:
        pid_after = await backend_pid(conn)
    return BodyErrorOutcome(error, pid_before, pid_after, after)


async def failing_batch_error(driver: Any, table: str) -> tuple[BaseException | None, int]:
    """(exception, rows left) for a batch whose third parameter set violates UNIQUE(name)."""
    batch = [("a", 1), ("b", 2), ("a", 3)]
    error: BaseException | None = None
    async with driver.connection() as conn:
        try:
            await conn.execute_many(f"INSERT INTO {table} (name, qty) VALUES (%s, %s)", batch)
        except Exception as exc:
            error = exc
    return error, await scratch_count(table)


# --------------------------------------------------------------------------- open failures
@dataclass(frozen=True)
class OpenFailure:
    error: BaseException | None
    elapsed: float
    stats: PoolStats
    capabilities_error: BaseException | None
    connection_error: BaseException | None


async def open_expecting_failure(driver: Any) -> OpenFailure:
    """`open()` a driver that cannot connect; describe how it fails and what is left."""
    result = await timed(driver.open())
    capabilities_error: BaseException | None = None
    try:
        _ = driver.capabilities
    except Exception as exc:
        capabilities_error = exc
    connection_error: BaseException | None = None
    try:
        async with driver.connection():
            pass
    except Exception as exc:
        connection_error = exc
    return OpenFailure(
        result.error, result.elapsed, driver.pool_stats(), capabilities_error, connection_error
    )


@dataclass(frozen=True)
class OpenComparison:
    async_failure: OpenFailure
    sync_error: BaseException | None


async def compare_open_failure(
    *,
    options: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    case: TzCase = UTC_CASE,
) -> OpenComparison:
    driver = build_driver(case=case, options=options, overrides=overrides)
    failure = await open_expecting_failure(driver)
    await driver.close()
    return OpenComparison(failure, await sync_connect_error(case, options, overrides))


@dataclass(frozen=True)
class ReopenOutcome:
    first_error: BaseException | None
    retry_error: BaseException | None
    stats_after_retry: PoolStats


async def reopen_after_password_change(role: str) -> ReopenOutcome:
    """Fail `open()` with a stale password, then retry on the same driver once it is valid."""
    async with temporary_login_role(role, "pw1"):
        driver = build_driver(overrides=login_overrides(role, "pw1"), pool={"timeout": 2})
        await set_role_password(role, "pw2")
        failed = await timed(driver.open(), limit=8)
        await set_role_password(role, "pw1")
        retried = await timed(driver.open(), limit=8)
        stats = driver.pool_stats()
        await driver.close()
    return ReopenOutcome(failed.error, retried.error, stats)


@dataclass(frozen=True)
class FirstBorrowOutcome:
    error: BaseException | None
    elapsed: float
    stats: PoolStats  # observed right after the failed borrow
    stats_after_close: PoolStats


async def first_borrow_after_login_revoked(role: str) -> FirstBorrowOutcome:
    """A `min_size=0` pool opens fine, then the role loses LOGIN; the first borrow must fail."""
    async with temporary_login_role(role, "pw1"):
        async with open_driver(
            overrides=login_overrides(role, "pw1"), pool={"min_size": 0, "timeout": 1}
        ) as driver:
            await set_role_login(role, login=False)

            async def borrow() -> None:
                async with driver.connection():
                    pass

            result = await timed(borrow())
            stats = driver.pool_stats()
            await driver.close()
            stats_after_close = driver.pool_stats()
        await set_role_login(role, login=True)
    return FirstBorrowOutcome(result.error, result.elapsed, stats, stats_after_close)
