"""Multi-step driver scenarios for MySQL/MariaDB tests (cancel, kill, pool pressure, streams).

Each scenario drives the driver through one situation and returns a frozen description of the
aftermath, so that the test body is a single `# when` / `# then` pair without control flow.
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import time
import uuid
import warnings
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from django.db import OperationalError

from asyncopation.drivers.base import PoolStats
from tests.support.aio import collect, wait_until
from tests.support.mysql_env import (
    SLEEP_SECONDS,
    Row,
    Timed,
    build_my_driver,
    connection_id,
    kill_thread,
    live_threads_settle_to,
    open_my_driver,
    settled_stats,
    statement_running,
    timed,
    tracked_pool_threads,
)

SERIES_ROWS = 500
SERIES_SQL = (
    f"WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < {SERIES_ROWS}) "
    "SELECT n FROM s ORDER BY n"
)
# Rows arrive one every 100 ms (wide enough that the server flushes each one instead of buffering
# them): draining the 500 of them would take 50 s.
SLOW_SERIES_SQL = f"SELECT n, REPEAT('x', 40000), SLEEP(0.1) FROM ({SERIES_SQL}) AS s"
EVERY_ROW = [(n,) for n in range(1, SERIES_ROWS + 1)]
SESSION_VALUES_SQL = (
    "SELECT @@transaction_isolation, @@autocommit, @@session.time_zone, @@global.time_zone"
)


def _marker() -> str:
    return f"asyncopation_{uuid.uuid4().hex[:12]}"


async def error_of(awaitable: Awaitable[Any]) -> BaseException | None:
    """The exception `awaitable` raises, or None."""
    try:
        await awaitable
    except Exception as exc:
        return exc
    return None


async def session_values(driver: Any) -> Row:
    """(isolation, autocommit, session time zone, global time zone) of a pooled connection."""
    async with driver.connection() as conn:
        return (await conn.fetch_all(SESSION_VALUES_SQL, None))[0]


async def user_variable(driver: Any, name: str) -> Any:
    async with driver.connection() as conn:
        return (await conn.fetch_all(f"SELECT @{name}", None))[0][0]


async def rows_of(driver: Any, sql: str, params: Any = None) -> list[Row]:
    async with driver.connection() as conn:
        return await conn.fetch_all(sql, params)


# --------------------------------------------------------------------------- open failures
@dataclass(frozen=True)
class OpenFailure:
    error: BaseException | None
    elapsed: float
    stats: PoolStats
    capabilities_error: BaseException | None
    connection_error: BaseException | None
    server_data_error: BaseException | None


@dataclass(frozen=True)
class OpenComparison:
    sync_error: BaseException | None
    async_failure: OpenFailure


async def open_expecting_failure(driver: Any) -> OpenFailure:
    result = await timed(driver.open())
    capabilities_error = await error_of(_read(lambda: driver.capabilities))
    server_data_error = await error_of(_read(lambda: driver.server_data))
    connection_error = await error_of(_borrow_once(driver))
    stats = driver.pool_stats()
    await driver.close()
    return OpenFailure(
        result.error,
        result.elapsed,
        stats,
        capabilities_error,
        connection_error,
        server_data_error,
    )


async def _read(getter: Callable[[], Any]) -> Any:
    return getter()


async def compare_open_failure(
    options: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None
) -> OpenComparison:
    """Open a driver with settings that cannot work; compare with Django's sync error."""
    from tests.support.mysql_reference import sync_connect_error

    driver = build_my_driver(options=options, overrides=overrides, pool={"min_size": 1})
    failure = await open_expecting_failure(driver)
    return OpenComparison(await sync_connect_error(options, overrides), failure)


# --------------------------------------------------------------------------- pool
async def _borrow_once(driver: Any) -> None:
    async with driver.connection():
        return None


@dataclass(frozen=True)
class PoolPressure:
    error: BaseException | None
    elapsed: float
    during: PoolStats
    after: PoolStats


async def acquire_while_saturated(driver: Any) -> PoolPressure:
    """A second borrower on a pool whose only connection is held: waits, then fails."""
    async with driver.connection():
        result = await timed(_borrow_once(driver))
        during = driver.pool_stats()
    return PoolPressure(result.error, result.elapsed, during, await settled_stats(driver))


async def _waiting_is(driver: Any, expected: int) -> bool:
    return driver.pool_stats().waiting == expected


async def overflow_waiting(driver: Any) -> PoolPressure:
    """With `max_waiting=1`: one borrower waits, the next is refused at once."""
    async with driver.connection():
        waiter = asyncio.create_task(_borrow_once(driver))
        await wait_until(lambda: _waiting_is(driver, 1))
        during = driver.pool_stats()
        refused = await timed(_borrow_once(driver))
        waiter.cancel()
        await asyncio.wait([waiter])
    return PoolPressure(refused.error, refused.elapsed, during, await settled_stats(driver))


async def stats_with_a_waiter(driver: Any) -> PoolPressure:
    """Pool counters while one borrower waits for the only connection, and once it is served."""
    async with driver.connection():
        waiter = asyncio.create_task(_borrow_once(driver))
        await wait_until(lambda: _waiting_is(driver, 1))
        during = driver.pool_stats()
    await asyncio.wait([waiter])
    return PoolPressure(waiter.exception(), 0.0, during, await settled_stats(driver, min_idle=1))


async def concurrent_connection_ids(driver: Any) -> list[int]:
    """Server thread ids of two connections borrowed at the same time."""
    async with driver.connection() as first, driver.connection() as second:
        return [await connection_id(first), await connection_id(second)]


@dataclass(frozen=True)
class LeakOutcome:
    stats: PoolStats
    expected_threads: int
    threads: int
    finished: int
    timed_out: int
    unexpected: int


async def _stress_task(driver: Any, deadline: float) -> str:
    try:
        async with asyncio.timeout(deadline), driver.connection() as conn:
            await conn.fetch_all("SELECT SLEEP(0.02)", None)
    except TimeoutError:
        return "timed_out"  # this task's own deadline
    except OperationalError as exc:
        return "timed_out" if _is_pool_refusal(exc) else "unexpected"
    except Exception:
        return "unexpected"
    return "finished"


def _is_pool_refusal(exc: OperationalError) -> bool:
    """The driver's own acquire timeout or `max_waiting` refusal, by its messages."""
    message = str(exc)
    return ("waiting for a connection" in message and "timed out after" in message) or (
        "too many acquires waiting" in message
    )


async def stress_pool_with_timeouts(
    *, tasks: int = 200, health_checks: bool = False, max_waiting: int = 0
) -> LeakOutcome:
    """Many tasks race the pool's acquire timeout and their own deadlines; count what leaks.

    A timeout (`TimeoutError`) or a refused acquire (`OperationalError`) is an expected outcome;
    anything else is `unexpected`. Leaks are counted per run: the server threads this pool opened.
    """
    # min_size == max_size: the pool is warm, so no deadline is spent on a cold connect
    pool = {"min_size": 3, "max_size": 3, "timeout": 0.2, "max_waiting": max_waiting}
    overrides = {"CONN_HEALTH_CHECKS": health_checks}
    with tracked_pool_threads() as opened:
        async with open_my_driver(pool=pool, overrides=overrides) as driver:
            deadlines = [0.06 + 0.01 * (i % 40) for i in range(tasks)]
            results = Counter(await asyncio.gather(*(_stress_task(driver, d) for d in deadlines)))
            stats = await settled_stats(driver, within=10.0)
            threads = await live_threads_settle_to(opened, stats.idle)
    return LeakOutcome(
        stats, stats.idle, threads, results["finished"], results["timed_out"], results["unexpected"]
    )


@dataclass(frozen=True)
class CloseOutcome:
    result: Timed[Any]
    stats: PoolStats


async def close_with_leaked_borrow(*, limit: float = 30.0) -> CloseOutcome:
    """`close()` while a connection is still borrowed (and never returned)."""
    driver = build_my_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    leaked = driver.connection()
    await leaked.__aenter__()
    result = await timed(driver.close(), limit=limit)
    return CloseOutcome(result, driver.pool_stats())


@dataclass(frozen=True)
class HealthOutcome:
    error: BaseException | None
    rows: list[Row] | None
    killed_id: int
    next_id: int | None


async def borrow_after_idle_thread_killed(*, health_checks: bool) -> HealthOutcome:
    """Kill the pooled connection's server thread while it is idle, then borrow again."""
    pool = {"min_size": 1, "max_size": 1, "timeout": 10}
    async with open_my_driver(pool=pool, overrides={"CONN_HEALTH_CHECKS": health_checks}) as driver:
        async with driver.connection() as conn:
            killed = await connection_id(conn)
        await kill_thread(killed)
        try:
            async with driver.connection() as conn:
                rows = await conn.fetch_all("SELECT 1", None)
                next_id = await connection_id(conn)
        except Exception as exc:
            return HealthOutcome(exc, None, killed, None)
    return HealthOutcome(None, rows, killed, next_id)


@dataclass(frozen=True)
class KillOutcome:
    error: BaseException | None
    killed_id: int
    next_id: int
    next_rows: list[Row]
    after: PoolStats


async def kill_then_query(driver: Any) -> KillOutcome:
    """Kill the borrowed connection's server thread, then use it; borrow again afterwards."""
    async with driver.connection() as conn:
        killed = await connection_id(conn)
        await kill_thread(killed)
        error = await error_of(conn.fetch_all("SELECT 1", None))
    after = await settled_stats(driver)
    async with driver.connection() as conn:
        next_id = await connection_id(conn)
        rows = await conn.fetch_all("SELECT 1", None)
    return KillOutcome(error, killed, next_id, rows, after)


# --------------------------------------------------------------------------- cancellation
@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool
    after: PoolStats
    held_id: int
    next_id: int
    next_rows: list[Row]
    next_autocommit: int
    final: PoolStats


async def cancel_during_sleep(
    driver: Any, *, transactional: bool = False, streaming: bool = False
) -> CancelOutcome:
    """Cancel a task that waits in `SLEEP(30)` (plain fetch, or a stream) and describe the aftermath."""
    marker = _marker()
    sql = f"SELECT SLEEP({SLEEP_SECONDS}) /* {marker} */"
    held: dict[str, int] = {}

    async def worker() -> None:
        async with driver.connection() as conn:
            held["id"] = await connection_id(conn)
            if transactional:
                await conn.begin()
            if streaming:
                async with conn.stream(sql, None, 10) as chunks:
                    await collect(chunks)
            else:
                await conn.fetch_all(sql, None)

    task = asyncio.create_task(worker())
    await wait_until(lambda: statement_running(marker))
    task.cancel()
    await asyncio.wait([task])
    after = await settled_stats(driver)
    async with driver.connection() as conn:
        next_id = await connection_id(conn)
        rows = await conn.fetch_all("SELECT 1", None)
        autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
    final = await settled_stats(driver, min_idle=1)
    return CancelOutcome(task.cancelled(), after, held["id"], next_id, rows, autocommit, final)


@dataclass(frozen=True)
class RollbackAfterCancel:
    rollback_error: BaseException | None
    next_rows: list[Row]
    next_autocommit: int
    final: PoolStats


async def rollback_after_cancel_in_transaction(driver: Any) -> RollbackAfterCancel:
    """Cancel a statement inside a transaction, call `rollback()` on the same connection, then
    borrow again."""
    marker = _marker()
    sql = f"SELECT SLEEP({SLEEP_SECONDS}) /* {marker} */"
    async with driver.connection() as conn:
        await conn.begin()
        task = asyncio.create_task(conn.fetch_all(sql, None))
        await wait_until(lambda: statement_running(marker))
        task.cancel()
        await asyncio.wait([task])
        error = await error_of(conn.rollback())
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT 1", None)
        autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
    final = await settled_stats(driver, min_idle=1)
    return RollbackAfterCancel(error, rows, autocommit, final)


# --------------------------------------------------------------------------- streams
async def stream_chunks(conn: Any, sql: str, chunk: int) -> list[list[Row]]:
    async with conn.stream(sql, None, chunk) as chunks:
        return await collect(chunks)


async def series_chunks(driver: Any, chunk: int) -> list[list[Row]]:
    async with driver.connection() as conn:
        return await stream_chunks(conn, SERIES_SQL, chunk)


def chunk_sizes(chunks: list[list[Row]]) -> list[int]:
    return [len(c) for c in chunks]


def flatten(chunks: list[list[Row]]) -> list[Row]:
    return [row for chunk in chunks for row in chunk]


@dataclass(frozen=True)
class EarlyExit:
    first_chunk: int
    same_connection: bool
    next_rows: list[Row]
    warnings: list[str]


async def stream_early_exit(driver: Any, chunk: int = 10) -> EarlyExit:
    """Leave a stream after its first chunk; use the connection again; record warnings."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        async with driver.connection() as conn:
            before = await connection_id(conn)
            async with conn.stream(SERIES_SQL, None, chunk) as chunks:
                first = await anext(chunks)
            rows = await conn.fetch_all("SELECT 1", None)
            after = await connection_id(conn)
        gc.collect()
    return EarlyExit(len(first), before == after, rows, [str(w.message) for w in caught])


# --------------------------------------------------------------------------- execute
async def failing_batch(driver: Any, table: str, sql: str, batch: list[tuple[Any, ...]]) -> Any:
    """(error, rows left in the table by name/qty) after `execute_many(sql, batch)`."""
    from tests.support.mysql_env import admin_query

    async with driver.connection() as conn:
        error = await error_of(conn.execute_many(sql, batch))
    rows = await admin_query(f"SELECT name, qty FROM {table} ORDER BY id")
    return error, rows


async def seed_four(driver: Any, table: str) -> None:
    """Rows a, b, c, d with qty 0 (one multi-row INSERT)."""
    async with driver.connection() as conn:
        await conn.execute(
            f"INSERT INTO {table} (name, qty) VALUES (%s, 0), (%s, 0), (%s, 0), (%s, 0)",
            ("a", "b", "c", "d"),
        )


# --------------------------------------------------------------------------- round 2 scenarios
async def released_connection_error(driver: Any, operation: str) -> BaseException | None:
    """The exception `operation` raises on a connection that was already returned to the pool."""
    async with driver.connection() as conn:
        pass
    calls = {
        "fetch_all": lambda: conn.fetch_all("SELECT 1", None),
        "execute": lambda: conn.execute("SELECT 1", None),
        "execute_many": lambda: conn.execute_many("SELECT 1", [()]),
        "begin": conn.begin,
        "commit": conn.commit,
        "rollback": conn.rollback,
        "savepoint": lambda: conn.savepoint("sp"),
        "release_savepoint": lambda: conn.release_savepoint("sp"),
        "rollback_to_savepoint": lambda: conn.rollback_to_savepoint("sp"),
        "stream": lambda: _enter_stream(conn),
    }
    return await error_of(calls[operation]())


async def _enter_stream(conn: Any) -> None:
    async with conn.stream("SELECT 1", None, 1):
        return None


async def stale_stream_iterator_error(driver: Any) -> BaseException | None:
    """Advance a stream's iterator after its connection went back to the pool."""
    async with driver.connection() as conn:
        async with conn.stream(SERIES_SQL, None, 5) as chunks:
            await anext(chunks)
        stale = chunks
    return await error_of(anext(stale))


@dataclass(frozen=True)
class ReleasedWithStream:
    next_rows: list[Row]
    same_connection: bool
    warnings: list[str]
    after: PoolStats


async def release_with_open_stream(driver: Any) -> ReleasedWithStream:
    """Return a connection while its stream is still open; borrow again."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        async with driver.connection() as conn:
            before = await connection_id(conn)
            stream = conn.stream(SERIES_SQL, None, 10)
            chunks = await stream.__aenter__()
            await anext(chunks)
        async with driver.connection() as conn:
            after_id = await connection_id(conn)
            rows = await conn.fetch_all("SELECT 1", None)
        await stream.__aexit__(None, None, None)
        gc.collect()
    stats = await settled_stats(driver, min_idle=1)
    return ReleasedWithStream(rows, before == after_id, [str(w.message) for w in caught], stats)


async def nested_statement_error(driver: Any, operation: str) -> BaseException | None:
    """Run `operation` on a connection whose stream is still open; the error it raises."""
    async with driver.connection() as conn, conn.stream(SERIES_SQL, None, 10) as chunks:
        await anext(chunks)
        calls = {
            "fetch_all": lambda: conn.fetch_all("SELECT 1", None),
            "execute": lambda: conn.execute("SELECT 1", None),
            "execute_many": lambda: conn.execute_many("SELECT 1", [()]),
            "stream": lambda: _enter_stream(conn),
            "begin": conn.begin,
            "commit": conn.commit,
        }
        return await error_of(calls[operation]())


async def nested_fetch_in_buffered_stream(driver: Any) -> tuple[list[Row], int]:
    """(rows of a statement run inside a buffered stream's body, rows the stream yields in all)."""
    async with driver.connection() as conn, conn.stream(SERIES_SQL, None, 10) as chunks:
        first = await anext(chunks)
        nested = await conn.fetch_all("SELECT 1", None)
        rest = await collect(chunks)
    return nested, len(first) + sum(len(c) for c in rest)


@dataclass(frozen=True)
class AbandonedStream:
    error: BaseException | None
    elapsed: float
    held_id: int
    next_id: int
    next_rows: list[Row]
    final: PoolStats
    next_autocommit: int


async def abandon_stream(
    driver: Any, *, cancel: bool, transactional: bool = False, slow: bool = False
) -> AbandonedStream:
    """End a stream's body early by an exception or by a cancel; describe the connection after."""
    held: dict[str, int] = {}
    started = asyncio.Event()

    async def worker() -> None:
        async with driver.connection() as conn:
            held["id"] = await connection_id(conn)
            if transactional:
                await conn.begin()
            async with conn.stream(SLOW_SERIES_SQL if slow else SERIES_SQL, None, 10) as chunks:
                await anext(chunks)
                started.set()
                if not cancel:
                    raise ValueError("body failed")
                await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    waiter.cancel()
    if cancel:
        task.cancel()
    started_at = time.monotonic()  # after the first chunk: only the leaving is timed
    await asyncio.wait([task])
    elapsed = time.monotonic() - started_at
    error = None if task.cancelled() else task.exception()
    async with driver.connection() as conn:
        next_id = await connection_id(conn)
        rows = await conn.fetch_all("SELECT 1", None)
        autocommit = (await conn.fetch_all("SELECT @@autocommit", None))[0][0]
    final = await settled_stats(driver, min_idle=1)
    return AbandonedStream(error, elapsed, held["id"], next_id, rows, final, autocommit)


@dataclass(frozen=True)
class DecoderFailure:
    error: BaseException | None
    held_id: int
    next_id: int
    next_rows: list[Row]
    final: PoolStats


TIME_OVERFLOW_SQL = "select cast('25:00:00' as time) union all select cast('01:00:00' as time)"


async def decoder_failure(driver: Any) -> DecoderFailure:
    """A result whose TIME value Django's decoder rejects; then borrow again."""
    async with driver.connection() as conn:
        held = await connection_id(conn)
        error = await error_of(conn.fetch_all(TIME_OVERFLOW_SQL, None))
    async with driver.connection() as conn:
        next_id = await connection_id(conn)
        rows = await conn.fetch_all("select 1", None)
    final = await settled_stats(driver, min_idle=1)
    return DecoderFailure(error, held, next_id, rows, final)


LATE_ERROR_SQL = f"SELECT n, IF(n = 3, (SELECT 1 UNION ALL SELECT 2), 0) FROM ({SERIES_SQL}) AS s"


async def error_during_fetch_in_transaction(driver: Any) -> BaseException | None:
    """A stream inside a transaction whose third row makes the server fail: the error raised
    while fetching the later chunks."""
    async with driver.connection() as conn:
        await conn.begin()
        stream = conn.stream(LATE_ERROR_SQL, None, 1)
        chunks = await stream.__aenter__()
        await anext(chunks)
        error = await error_of(collect(chunks))
        await error_of(stream.__aexit__(None, None, None))
    return error


async def django_class_of_late_error() -> type[BaseException] | None:
    """The class Django's sync mysqlclient cursor raises for the same statement."""
    from tests.support.mysql_reference import sync_rows

    error = await error_of(sync_rows(None, LATE_ERROR_SQL))
    return None if error is None else type(error)


async def variable_on_two_connections(options: dict[str, Any], name: str) -> list[Any]:
    """`@name` on two connections borrowed at the same time from a pool of two."""
    pool = {"min_size": 2, "max_size": 2}
    async with (
        open_my_driver(options=options, pool=pool) as driver,
        driver.connection() as first,
        driver.connection() as second,
    ):
        return [
            (await first.fetch_all(f"SELECT @{name}", None))[0][0],
            (await second.fetch_all(f"SELECT @{name}", None))[0][0],
        ]


@dataclass(frozen=True)
class SurvivedBody:
    body_error: BaseException | None
    same_connection: bool
    committed: list[str]


async def stream_body_error_inside_a_transaction(driver: Any, table: str) -> SurvivedBody:
    """begin, insert a, savepoint, insert b, a stream whose body raises (caught), roll back to the
    savepoint, commit: the transaction must have survived the body's exception."""
    from tests.support.mysql_env import insert_named, scratch_names

    async with driver.connection() as conn:
        before = await connection_id(conn)
        await conn.begin()
        await insert_named(conn, table, "a")
        await conn.savepoint("sp")
        await insert_named(conn, table, "b")
        body_error = await error_of(_stream_body_raises(conn))
        await conn.rollback_to_savepoint("sp")
        await conn.commit()
        same = before == await connection_id(conn)
    return SurvivedBody(body_error, same, await scratch_names(table))


async def _stream_body_raises(conn: Any) -> None:
    async with conn.stream(SERIES_SQL, None, 10) as chunks:
        await anext(chunks)
        raise ValueError("body failed")


BAD_PARAMS_SQL = "select %s, %s"


@dataclass(frozen=True)
class BadParameters:
    error: BaseException | None
    same_connection: bool
    next_rows: list[Row]
    committed: list[str]


async def bad_parameter_count_inside_a_transaction(
    driver: Any, table: str, operation: str
) -> BadParameters:
    """A statement with too few parameters inside a transaction, then keep using the transaction."""
    from tests.support.mysql_env import insert_named, scratch_names

    async with driver.connection() as conn:
        before = await connection_id(conn)
        await conn.begin()
        await insert_named(conn, table, "a")
        calls = {
            "fetch_all": lambda: conn.fetch_all(BAD_PARAMS_SQL, (1,)),
            "execute": lambda: conn.execute(BAD_PARAMS_SQL, (1,)),
            "stream": lambda: _stream_bad_params(conn),
        }
        error = await error_of(calls[operation]())
        rows = await conn.fetch_all("SELECT 1", None)
        await conn.commit()
        same = before == await connection_id(conn)
    return BadParameters(error, same, rows, await scratch_names(table))


async def _stream_bad_params(conn: Any) -> None:
    async with conn.stream(BAD_PARAMS_SQL, (1,), 1) as chunks:
        await collect(chunks)


async def buffered_begin_and_commit_inside_a_stream(driver: Any) -> list[BaseException | None]:
    """`begin()` then `commit()` in the body of a buffered stream: their errors."""
    async with driver.connection() as conn, conn.stream(SERIES_SQL, None, 10) as chunks:
        await anext(chunks)
        return [await error_of(conn.begin()), await error_of(conn.commit())]


BAD_BATCHES = {
    "update": ("UPDATE {t} SET qty = %s WHERE qty = %s", [(1,)]),
    "insert": ("INSERT INTO {t} (name, qty) VALUES (%s, %s)", [("x",)]),
}


async def bad_execute_many_inside_a_transaction(
    driver: Any, table: str, kind: str
) -> BadParameters:
    """`execute_many` with too few parameters inside a transaction; then keep using it."""
    from tests.support.mysql_env import insert_named, scratch_names

    template, batch = BAD_BATCHES[kind]
    async with driver.connection() as conn:
        before = await connection_id(conn)
        await conn.begin()
        await insert_named(conn, table, "a")
        error = await error_of(conn.execute_many(template.format(t=table), batch))
        rows = await conn.fetch_all("SELECT 1", None)
        await conn.commit()
        same = before == await connection_id(conn)
    return BadParameters(error, same, rows, await scratch_names(table))


def shrink_recovery_timeout(monkeypatch: Any, seconds: float) -> None:
    """The drain bound of the connection (`_RECOVERY_TIMEOUT`) for one test."""
    monkeypatch.setattr("asyncopation.drivers.mysql.connection._RECOVERY_TIMEOUT", seconds)


@dataclass(frozen=True)
class SlowDrain:
    elapsed: float
    same_connection: bool
    next_rows: list[Row]


async def leave_slow_stream(driver: Any, *, via_release: bool) -> SlowDrain:
    """Leave a stream of slow rows after its first chunk: by leaving its `async with` (`break`),
    or by returning the connection while the stream is still open (`reset()`). Time the leaving."""
    if via_release:
        return await _return_connection_with_open_stream(driver)
    return await _break_out_of_stream(driver)


async def _after_leaving(driver: Any, held: int, elapsed: float) -> SlowDrain:
    async with driver.connection() as conn:
        next_id = await connection_id(conn)
        rows = await conn.fetch_all("SELECT 1", None)
    return SlowDrain(elapsed, held == next_id, rows)


async def _break_out_of_stream(driver: Any) -> SlowDrain:
    async with driver.connection() as conn:
        held = await connection_id(conn)
        async with conn.stream(SLOW_SERIES_SQL, None, 1) as chunks:
            await anext(chunks)
            started = time.monotonic()
        elapsed = time.monotonic() - started
    return await _after_leaving(driver, held, elapsed)


async def _return_connection_with_open_stream(driver: Any) -> SlowDrain:
    borrow = driver.connection()
    conn = await borrow.__aenter__()
    held = await connection_id(conn)
    stream = conn.stream(SLOW_SERIES_SQL, None, 1)
    chunks = await stream.__aenter__()
    await anext(chunks)
    started = time.monotonic()
    await borrow.__aexit__(None, None, None)
    elapsed = time.monotonic() - started
    await error_of(stream.__aexit__(None, None, None))
    return await _after_leaving(driver, held, elapsed)


NAN_SQL = "SELECT %s"


async def nan_parameter(
    driver: Any, table: str, operation: str, *, transactional: bool
) -> BadParameters:
    """A NaN parameter (Django: 'nan can not be used with MySQL'), then keep using the connection."""
    from tests.support.mysql_env import insert_named, scratch_names

    nan = float("nan")
    async with driver.connection() as conn:
        before = await connection_id(conn)
        if transactional:
            await conn.begin()
            await insert_named(conn, table, "a")
        calls = {
            "fetch_all": lambda: conn.fetch_all(NAN_SQL, (nan,)),
            "execute": lambda: conn.execute(NAN_SQL, (nan,)),
            "stream": lambda: _stream_nan(conn, nan),
        }
        error = await error_of(calls[operation]())
        rows = await conn.fetch_all("SELECT 1", None)
        if transactional:
            await conn.commit()
        same = before == await connection_id(conn)
    return BadParameters(error, same, rows, await scratch_names(table))


async def _stream_nan(conn: Any, nan: float) -> None:
    async with conn.stream(NAN_SQL, (nan,), 1) as chunks:
        await collect(chunks)


async def probe_connection_state_after_open() -> tuple[int, list[bool]]:
    """(probe connections made by `open()`, whether each was closed when `open()` returned)."""
    from tests.support.mysql_env import tracked_direct_connections

    with tracked_direct_connections() as seen:
        async with open_my_driver(pool={"min_size": 1, "max_size": 1}):
            return len(seen), [not conn.connected for conn in seen]


async def asyncmy_acquire_after_close_error() -> BaseException | None:
    """What asyncmy's own pool raises for an acquire after `close()` (the driver relies on it)."""
    import asyncmy

    from asyncopation.drivers.mysql.settings import build_config
    from tests.support.mysql_env import my_settings

    config = build_config("default", my_settings(), use_tz=True, time_zone="UTC")
    pool = await asyncmy.create_pool(
        minsize=1, maxsize=1, **config.pooled_kwargs(sql_auto_is_null=False)
    )
    pool.close()
    await pool.wait_closed()
    return await error_of(pool.acquire())


async def asyncmy_release_in_transaction_drops_the_connection() -> tuple[int, int]:
    """(pool size before, after) releasing a connection that is inside a transaction."""
    import asyncmy

    from asyncopation.drivers.mysql.settings import build_config
    from tests.support.mysql_env import my_settings

    config = build_config("default", my_settings(), use_tz=True, time_zone="UTC")
    pool = await asyncmy.create_pool(
        minsize=1, maxsize=1, **config.pooled_kwargs(sql_auto_is_null=False)
    )
    try:
        conn = await pool.acquire()
        await conn.begin()
        before = pool.size
        await pool.release(conn)
        return before, pool.size
    finally:
        pool.close()
        await pool.wait_closed()


async def client_error_inside_a_transaction(
    table: str,
    operation: str,
    sql: str,
    params: Any,
    options: dict[str, Any] | None = None,
) -> BadParameters:
    """A parameter the client side rejects (a non-string set, a non-finite float, a missing
    pyformat key) through `operation` inside a transaction, then keep using the connection."""
    from tests.support.mysql_env import insert_named, open_my_driver, scratch_names

    async with (
        open_my_driver(options=options, pool={"min_size": 1, "max_size": 1}) as driver,
        driver.connection() as conn,
    ):
        before = await connection_id(conn)
        await conn.begin()
        await insert_named(conn, table, "a")
        calls = {
            "fetch_all": lambda: conn.fetch_all(sql, params),
            "execute": lambda: conn.execute(sql, params),
            "execute_many": lambda: conn.execute_many(sql, [params]),
            "stream": lambda: _stream_value(conn, sql, params),
        }
        error = await error_of(calls[operation]())
        rows = await conn.fetch_all("SELECT 1", None)
        await conn.commit()
        same = before == await connection_id(conn)
    return BadParameters(error, same, rows, await scratch_names(table))


async def _stream_value(conn: Any, sql: str, params: Any) -> None:
    async with conn.stream(sql, params, 1) as chunks:
        await collect(chunks)


async def many_with_a_percent_in_the_insert_postfix(
    driver: Any, table: str
) -> tuple[int, list[str]]:
    """`execute_many` of an INSERT that the driver rewrites, whose ON DUPLICATE postfix holds a
    literal '%' (sent verbatim, never formatted): (rowcount, stored names)."""
    from tests.support.mysql_env import scratch_names

    sql = f"INSERT INTO {table} (name, qty) VALUES (%s, %s) ON DUPLICATE KEY UPDATE name = CONCAT(name, '%')"
    async with driver.connection() as conn:
        result = await conn.execute_many(sql, [("a", 1), ("b", 2)])
    return result.rowcount, await scratch_names(table)


async def bad_batch(
    table: str, template: str, batch: list[Any], *, transactional: bool
) -> BadParameters:
    """`execute_many(template, batch)` where a later set is rejected by the client, against a table
    holding the row ('a', 0): the error, whether the connection survived, the rows afterwards."""
    from tests.support.mysql_env import insert_named, open_my_driver, scratch_names

    sql = template.format(t=table)
    async with (
        open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver,
        driver.connection() as conn,
    ):
        before = await connection_id(conn)
        await insert_named(conn, table, "a")
        if transactional:
            await conn.begin()
        error = await error_of(conn.execute_many(sql, batch))
        rows = await conn.fetch_all(f"SELECT name, qty FROM {table} ORDER BY name", None)
        if transactional:
            await conn.commit()
        same = before == await connection_id(conn)
    return BadParameters(error, same, rows, await scratch_names(table))


async def cancelled_between_statements(table: str) -> tuple[bool, bool, int]:
    """A per-row batch of 20 statements, the fourth one slow, cancelled by a timeout: (the cancel was a timeout,
    the next borrow got a new connection, how many of the 20 statements had been applied)."""
    from tests.support.mysql_env import admin_query, open_my_driver

    await admin_query(f"INSERT INTO {table} (name, qty) VALUES ('a', 0)")
    sql = f"UPDATE {table} SET qty = qty + 1 + SLEEP(%s) * 0 WHERE name = 'a'"
    async with open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver:
        async with driver.connection() as conn:
            before = await connection_id(conn)
            try:
                async with asyncio.timeout(0.5):
                    await conn.execute_many(sql, [(0,)] * 3 + [(2,)] + [(0,)] * 16)
            except TimeoutError:
                timed_out = True
            else:
                timed_out = False
        async with driver.connection() as again:
            replaced = before != await connection_id(again)
    applied = (await admin_query(f"SELECT qty FROM {table} WHERE name = 'a'"))[0][0]
    return timed_out, replaced, applied


@dataclass(frozen=True)
class AsyncmyConnectionFacts:
    bytes_statement_hex: str
    mogrify_without_args: str
    encoding: Any
    server_status_is_int: bool
    no_backslash_flag_after_set: bool
    literal: str
    escape_string: str
    mogrified_bytes_statement: bytes
    pool_size_attribute_types: tuple[type, type]
    stmt_cache_size_default: Any


async def asyncmy_connection_facts() -> AsyncmyConnectionFacts:
    """What the driver relies on in asyncmy's own `Connection` (see ADR-0003 Decision 5)."""
    import asyncmy
    from asyncmy.constants.SERVER_STATUS import SERVER_STATUS_NO_BACKSLASH_ESCAPES

    from asyncopation.drivers.mysql.settings import build_config
    from tests.support.mysql_env import my_settings

    config = build_config("default", my_settings(), use_tz=True, time_zone="UTC")
    conn: Any = await asyncmy.connect(**config.pooled_kwargs(sql_auto_is_null=False))
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(
                b"SELECT HEX('" + "\u20ac".encode() + b"')"
            )  # bytes: not re-encoded
            hex_text = (await cursor.fetchall())[0][0]
            mogrified = cursor.mogrify("SELECT '%s' /* 5% */", None)
            carried = cursor.mogrify("SELECT %s", (b"\xff",))
            await cursor.execute(
                "SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES')"
            )
            await cursor.execute("DO 0")  # a reply that carries the status flags
        return AsyncmyConnectionFacts(
            bytes_statement_hex=hex_text,
            mogrify_without_args=mogrified,
            encoding=conn._encoding,
            server_status_is_int=isinstance(conn.server_status, int),
            no_backslash_flag_after_set=bool(
                conn.server_status & SERVER_STATUS_NO_BACKSLASH_ESCAPES
            ),
            literal=conn.literal("a\\b"),
            escape_string=conn.escape_string("a'b"),
            mogrified_bytes_statement=carried.encode(conn._encoding, "surrogateescape"),
            pool_size_attribute_types=await _pool_size_attribute_types(config),
            stmt_cache_size_default=inspect.signature(asyncmy.connect)
            .parameters["stmt_cache_size"]
            .default,
        )
    finally:
        await conn.ensure_closed()


async def _pool_size_attribute_types(config: Any) -> tuple[type, type]:
    import asyncmy

    pool = await asyncmy.create_pool(
        minsize=1, maxsize=1, **config.pooled_kwargs(sql_auto_is_null=False)
    )
    try:
        return type(pool.size), type(pool.freesize)
    finally:
        pool.close()
        await pool.wait_closed()
