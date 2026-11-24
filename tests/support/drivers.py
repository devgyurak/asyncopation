"""Helpers for driver tests: driver factories, admin connection, reference (sync) rows, probes.

All logic that the driver test modules would otherwise need as control flow lives here. The
PostgreSQL driver is imported lazily so that modules which only need the vocabulary (unit tests)
import without it.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import uuid
from collections.abc import AsyncGenerator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any

import psycopg
import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.test import override_settings

from asyncopation.drivers.base import AsyncDbConnection, AsyncDriver, PoolStats
from harness.settings import VENDOR
from tests.support.aio import collect, wait_until

POSTGRES_ONLY = pytest.mark.skipif(VENDOR != "postgresql", reason="PostgreSQL driver tests")

SCRATCH_TABLE = "asyncopation_scratch"
SCRATCH_DDL = (
    "id serial PRIMARY KEY, name text NOT NULL UNIQUE, "
    "qty integer NOT NULL DEFAULT 0 CHECK (qty >= 0)"
)

type Row = tuple[Any, ...]


# --------------------------------------------------------------------------- vocabulary
@dataclass(frozen=True)
class TzCase:
    """One cell of the USE_TZ / TIME_ZONE matrix (ADR-0003 validation)."""

    use_tz: bool
    db_time_zone: str | None  # DATABASES[alias]["TIME_ZONE"]
    settings_time_zone: str  # settings.TIME_ZONE

    @property
    def time_zone(self) -> str:
        """The connection time zone as Django's `DatabaseWrapper.timezone_name` computes it."""
        if not self.use_tz:
            return self.settings_time_zone
        return self.db_time_zone or "UTC"


UTC_CASE = TzCase(True, None, "UTC")


@dataclass(frozen=True)
class Binding:
    """`server_side_binding` OPTIONS variant."""

    options: dict[str, Any]
    server_side: bool


@dataclass(frozen=True)
class RowsComparison:
    async_rows: list[Row]
    sync_rows: list[Row]


# --------------------------------------------------------------------------- static conformance
def as_driver(driver: AsyncDriver) -> AsyncDriver:
    """Static check: the argument must satisfy the `AsyncDriver` protocol."""
    return driver


def as_connection(connection: AsyncDbConnection) -> AsyncDbConnection:
    """Static check: the argument must satisfy the `AsyncDbConnection` protocol."""
    return connection


def set_attribute(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


def replace_field(obj: Any, **changes: Any) -> Any:
    return dataclasses.replace(obj, **changes)


# --------------------------------------------------------------------------- settings and drivers
def pg_settings(
    *,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    db_time_zone: str | None = None,
) -> dict[str, Any]:
    """A Django DATABASES entry for the test database, with OPTIONS merged."""
    settings_dict = copy.deepcopy(dict(connections["default"].settings_dict))
    merged = {**settings_dict.get("OPTIONS", {}), **(options or {})}
    if pool is not None:
        merged["pool"] = pool
    settings_dict["OPTIONS"] = merged
    settings_dict["TIME_ZONE"] = db_time_zone
    return settings_dict


def build_driver(
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
) -> Any:
    """A `PostgresDriver` that is not opened yet."""
    from asyncopation.drivers.postgresql import PostgresDriver

    settings_dict = pg_settings(options=options, pool=pool, db_time_zone=case.db_time_zone)
    return PostgresDriver.from_settings(
        "default", settings_dict, use_tz=case.use_tz, time_zone=case.time_zone
    )


@asynccontextmanager
async def open_driver(
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
) -> AsyncGenerator[Any]:
    driver = build_driver(case=case, options=options, pool=pool)
    await driver.open()
    try:
        yield driver
    finally:
        await driver.close()


# --------------------------------------------------------------------------- admin connection
async def admin_query(sql: str, params: Sequence[Any] | None = None) -> list[Row]:
    """Run one statement on a separate autocommit connection (rows, or [] without a result)."""
    cfg = connections["default"].settings_dict
    async with (
        await psycopg.AsyncConnection.connect(
            dbname=cfg["NAME"],
            user=cfg["USER"],
            password=cfg["PASSWORD"],
            host=cfg["HOST"],
            port=cfg["PORT"],
            autocommit=True,
        ) as admin,
        admin.cursor() as cursor,
    ):
        await cursor.execute(sql, params)  # ty: ignore[invalid-argument-type]
        if cursor.description is None:
            return []
        return [tuple(row) for row in await cursor.fetchall()]


async def admin_scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    return (await admin_query(sql, params))[0][0]


async def create_scratch_table() -> str:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
    await admin_query(f"CREATE TABLE {SCRATCH_TABLE} ({SCRATCH_DDL})")
    return SCRATCH_TABLE


async def drop_scratch_table() -> None:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")


async def scratch_count(table: str = SCRATCH_TABLE) -> int:
    return await admin_scalar(f"SELECT count(*) FROM {table}")


async def scratch_names(table: str = SCRATCH_TABLE) -> list[str]:
    return [row[0] for row in await admin_query(f"SELECT name FROM {table} ORDER BY id")]


@asynccontextmanager
async def temporary_role(name: str) -> AsyncGenerator[str]:
    await admin_query(f"DROP ROLE IF EXISTS {name}")
    await admin_query(f"CREATE ROLE {name}")
    try:
        yield name
    finally:
        await admin_query(f"DROP ROLE IF EXISTS {name}")


async def server_version_tuple() -> tuple[int, int]:
    number = int(await admin_scalar("SHOW server_version_num"))
    major, minor = divmod(number, 10000)
    return major, minor


async def backend_exists(pid: int) -> bool:
    return bool(await admin_scalar("SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,)))


async def wait_backend_gone(pid: int) -> None:
    async def gone() -> bool:
        return not await backend_exists(pid)

    await wait_until(gone)


async def backend_ends(pid: int) -> bool:
    """Wait until the backend is gone; True once it is (TimeoutError otherwise)."""
    await wait_backend_gone(pid)
    return True


def holdable_flags(cursors: Iterable[CursorRow]) -> list[bool]:
    return [c.is_holdable for c in cursors]


def scrollable_flags(cursors: Iterable[CursorRow]) -> list[bool]:
    return [c.is_scrollable for c in cursors]


async def terminate_backend(pid: int) -> None:
    await admin_query("SELECT pg_terminate_backend(%s)", (pid,))
    await wait_backend_gone(pid)


# --------------------------------------------------------------------------- connection probes
async def backend_pid(conn: AsyncDbConnection) -> int:
    return (await conn.fetch_all("SELECT pg_backend_pid()", None))[0][0]


async def session_setting(conn: AsyncDbConnection, name: str) -> str:
    return (await conn.fetch_all(f"SHOW {name}", None))[0][0]


async def in_transaction(conn: AsyncDbConnection) -> bool:
    """True when the session sits inside an open transaction (two statements are compared)."""
    await conn.fetch_all("SELECT 1", None)
    rows = await conn.fetch_all(
        "SELECT xact_start <> query_start FROM pg_stat_activity WHERE pid = pg_backend_pid()", None
    )
    return rows[0][0]


async def scratch_rows(conn: AsyncDbConnection, table: str = SCRATCH_TABLE) -> list[Row]:
    return await conn.fetch_all(f"SELECT name, qty FROM {table} ORDER BY id", None)


async def insert_named(conn: AsyncDbConnection, table: str, name: str, qty: int = 0) -> None:
    await conn.execute(f"INSERT INTO {table} (name, qty) VALUES (%s, %s)", (name, qty))


async def query_text_seen_by_server(conn: AsyncDbConnection) -> str:
    """The text of the statement as the server received it (shows `$1` under server binding)."""
    sql = "SELECT query FROM pg_stat_activity WHERE pid = pg_backend_pid() AND %s::int = 7"
    return (await conn.fetch_all(sql, (7,)))[0][0]


async def prepared_probe_count(conn: AsyncDbConnection) -> int:
    await conn.fetch_all("SELECT 12345 + %s::int AS prepare_probe", (1,))
    sql = "SELECT count(*) FROM pg_prepared_statements WHERE statement LIKE %s"
    return (await conn.fetch_all(sql, ("%prepare_probe%",)))[0][0]


def uses_placeholders(query_text: str) -> bool:
    return "$1" in query_text


def all_plain_tuples(rows: Iterable[Any]) -> bool:
    return all(type(row) is tuple for row in rows)


# --------------------------------------------------------------------------- streams
@dataclass(frozen=True)
class CursorRow:
    name: str
    statement: str
    is_holdable: bool
    is_scrollable: bool


async def open_cursors(conn: AsyncDbConnection) -> list[CursorRow]:
    rows = await conn.fetch_all(
        "SELECT name, statement, is_holdable, is_scrollable FROM pg_cursors ORDER BY name", None
    )
    return [CursorRow(*row) for row in rows]


async def stream_chunks(
    conn: AsyncDbConnection, sql: str, params: Any, chunk: int
) -> list[list[Row]]:
    async with conn.stream(sql, params, chunk) as chunks:
        return await collect(chunks)


def sizes(chunks: Sequence[Sequence[Any]]) -> list[int]:
    return [len(c) for c in chunks]


def flatten(chunks: Iterable[Iterable[Row]]) -> list[Row]:
    return [row for c in chunks for row in c]


@dataclass(frozen=True)
class StreamObservation:
    chunks_read: list[list[Row]]
    cursors_open: list[CursorRow]
    cursors_after: list[CursorRow]


async def observe_stream(
    conn: AsyncDbConnection,
    sql: str,
    params: Any,
    chunk: int,
    *,
    reads: int = 1,
    transactional: bool = False,
) -> StreamObservation:
    """Read `reads` chunks, then list `pg_cursors` while open and after the stream exited."""
    if transactional:
        await conn.begin()
    read: list[list[Row]] = []
    async with conn.stream(sql, params, chunk) as chunks:
        for _ in range(reads):
            read.append(await anext(chunks))
        during = await open_cursors(conn)
    after = await open_cursors(conn)
    if transactional:
        await conn.rollback()
    return StreamObservation(read, during, after)


@dataclass(frozen=True)
class FailedStream:
    error: BaseException | None
    cursors_after: list[CursorRow]


async def observe_failed_stream(conn: AsyncDbConnection, sql: str, chunk: int) -> FailedStream:
    """Raise inside the stream body after the first chunk; the cursor must still be closed."""
    error: BaseException | None = None
    try:
        async with conn.stream(sql, None, chunk) as chunks:
            await anext(chunks)
            raise RuntimeError("boom")
    except RuntimeError as exc:
        error = exc
    return FailedStream(error, await open_cursors(conn))


# --------------------------------------------------------------------------- atomic execute_many
@dataclass(frozen=True)
class AtomicityObservation:
    capability: bool
    rows_left: int


async def observe_failing_batch(driver: Any, table: str) -> AtomicityObservation:
    batch = [("a", 1), ("b", 2), ("a", 3)]  # the third row violates UNIQUE(name)
    try:
        async with driver.connection() as conn:
            await conn.execute_many(f"INSERT INTO {table} (name, qty) VALUES (%s, %s)", batch)
    except Exception:
        pass
    return AtomicityObservation(driver.capabilities.atomic_execute_many, await scratch_count(table))


# --------------------------------------------------------------------------- cancellation
async def _probe_active(marker: str) -> bool:
    count = await admin_scalar(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE query LIKE %s AND pid <> pg_backend_pid() AND state = 'active'",
        (f"%{marker}%",),
    )
    return count > 0


async def _probe_gone(marker: str) -> bool:
    return not await _probe_active(marker)


@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool
    before: PoolStats
    after: PoolStats
    next_rows: list[Row]
    next_in_transaction: bool
    statement_stopped: bool


async def cancel_during_sleep(driver: Any, *, transactional: bool = False) -> CancelOutcome:
    """Cancel a task that sits in `pg_sleep(30)` and describe the aftermath."""
    marker = f"probe_{uuid.uuid4().hex}"
    sql = f"SELECT pg_sleep(30) /* {marker} */"
    before = driver.pool_stats()

    async def worker() -> None:
        async with driver.connection() as conn:
            if transactional:
                await conn.begin()
            await conn.fetch_all(sql, None)

    task = asyncio.create_task(worker())

    async def started() -> bool:
        return await _probe_active(marker)

    await wait_until(started)
    task.cancel()
    await asyncio.wait([task])
    after = driver.pool_stats()
    stopped = await _stopped(marker)
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT 1", None)
        in_txn = await in_transaction(conn)
    return CancelOutcome(task.cancelled(), before, after, rows, in_txn, stopped)


async def _stopped(marker: str) -> bool:
    async def gone() -> bool:
        return await _probe_gone(marker)

    try:
        await wait_until(gone, within=5.0)
    except TimeoutError:
        return False
    return True


@dataclass(frozen=True)
class WaiterOutcome:
    cancelled: bool
    waiting_seen: int
    after: PoolStats
    reusable_rows: list[Row]


async def cancel_waiting_borrower(driver: Any) -> WaiterOutcome:
    """Saturate a one-connection pool, cancel a borrower that waits, release, inspect the pool."""

    async def waiter() -> None:
        async with driver.connection():
            return

    async with driver.connection():
        task = asyncio.create_task(waiter())

        async def queued() -> bool:
            return driver.pool_stats().waiting == 1

        await wait_until(queued)
        waiting_seen = driver.pool_stats().waiting
        task.cancel()
        await asyncio.wait([task])
    after = driver.pool_stats()
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT 1", None)
    return WaiterOutcome(task.cancelled(), waiting_seen, after, rows)


# --------------------------------------------------------------------------- disconnects
@dataclass(frozen=True)
class KillOutcome:
    error: BaseException | None
    killed_pid: int
    after: PoolStats
    fresh_pid: int
    fresh_rows: list[Row]


async def kill_backend_then_query(driver: Any, *, transactional: bool = False) -> KillOutcome:
    """Terminate the backend of a borrowed connection, use it, then borrow again."""
    error: BaseException | None = None
    pid = 0
    try:
        async with driver.connection() as conn:
            if transactional:
                await conn.begin()
            pid = await backend_pid(conn)
            await terminate_backend(pid)
            await conn.fetch_all("SELECT 1", None)
    except Exception as exc:
        error = exc
    # The pool replaces a discarded connection in a background task: wait until it has settled.
    await wait_until(lambda: _settled(driver), within=5.0)
    after = driver.pool_stats()
    async with driver.connection() as fresh:
        fresh_pid = await backend_pid(fresh)
        fresh_rows = await fresh.fetch_all("SELECT 1", None)
    return KillOutcome(error, pid, after, fresh_pid, fresh_rows)


async def _settled(driver: Any) -> bool:
    stats = driver.pool_stats()
    return stats.in_use == 0 and stats.waiting == 0


def mapped_from(exc: Exception, dbapi: ModuleType = psycopg) -> Exception:
    """The `django.db` exception for `exc` the way the driver raises it (with `__cause__`)."""
    from asyncopation.drivers.errors import raise_mapped

    try:
        raise_mapped(exc, dbapi)
    except Exception as mapped:
        return mapped
    raise AssertionError("raise_mapped must raise")


# --------------------------------------------------------------------------- Django error drift
class _StubWrapper:
    """The two attributes `DatabaseErrorWrapper` touches on a connection wrapper."""

    def __init__(self, dbapi: ModuleType) -> None:
        self.Database = dbapi
        self.errors_occurred = False


def ported(name: str) -> Any:
    """Upstream Django object the driver ports (`_django.postgresql.ported_references`)."""
    from asyncopation._django.postgresql import ported_references

    return ported_references()[name]


def psycopg_error_classes() -> list[type[psycopg.Error]]:
    """Every exception class of psycopg's hierarchy (top-level and `psycopg.errors`)."""
    import psycopg.errors

    found: dict[str, type[psycopg.Error]] = {}
    for module in (psycopg, psycopg.errors):
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, psycopg.Error):
                found[name] = obj
    return [found[name] for name in sorted(found)]


def django_wrapper_class(exc: Exception, dbapi: ModuleType = psycopg) -> type[BaseException]:
    """The exception class Django's own `DatabaseErrorWrapper` raises for `exc`."""
    DatabaseErrorWrapper = ported("DatabaseErrorWrapper")
    try:
        with DatabaseErrorWrapper(_StubWrapper(dbapi)):
            raise exc
    except Exception as raised:
        return type(raised)
    raise AssertionError("DatabaseErrorWrapper must raise")


def our_mapped_class(exc: Exception, dbapi: ModuleType = psycopg) -> type[BaseException] | None:
    from asyncopation.drivers.errors import map_exception

    mapped = map_exception(exc, dbapi)
    return None if mapped is None else type(mapped)


def instance_of(cls: type[Exception], *args: Any) -> Exception:
    return cls(*args)


def custom_subclass(base: type[Exception]) -> type[Exception]:
    return type("CustomDriverError", (base,), {})


# --------------------------------------------------------------------------- reference rows
def field_zoo_sql(case: TzCase = UTC_CASE) -> str:
    """All FieldZoo rows; outside UTC the year-9999 row is left out (it overflows to year 10000)."""
    from harness.catalog.models import FieldZoo

    where = "" if case.time_zone == "UTC" else " WHERE \"char\" <> 'max'"
    columns = ", ".join(f'"{f.column}"' for f in FieldZoo._meta.concrete_fields)
    return f"SELECT {columns} FROM {FieldZoo._meta.db_table}{where} ORDER BY id"


def field_zoo_row_count(case: TzCase = UTC_CASE) -> int:
    return 4 if case.time_zone == "UTC" else 3


def _plain(value: Any) -> str:
    if isinstance(value, memoryview):
        value = bytes(value)
    return repr(value)


def normalise(rows: Iterable[Iterable[Any]]) -> list[Row]:
    """Rows as (type name, repr) pairs so that value *and* type are compared."""
    return [tuple((type(v).__qualname__, _plain(v)) for v in row) for row in rows]


async def sync_rows(
    case: TzCase, options: dict[str, Any] | None, sql: str, params: Any = None
) -> list[Row]:
    """The rows Django's own sync PostgreSQL cursor returns (fresh wrapper, own connection)."""

    def run() -> list[Row]:
        from django.db.backends.postgresql.base import DatabaseWrapper

        settings_dict = pg_settings(options=options, db_time_zone=case.db_time_zone)
        with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
            wrapper = DatabaseWrapper(settings_dict, alias="drivers_reference")
            try:
                with wrapper.cursor() as cursor:
                    cursor.execute(sql, params)
                    return [tuple(row) for row in cursor.fetchall()]
            finally:
                wrapper.close()

    return await sync_to_async(run)()


async def compare_with_sync(
    sql: str,
    params: Any = None,
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
) -> RowsComparison:
    """Run `sql` through the async driver and through Django's sync cursor; normalise both."""
    async with open_driver(case=case, options=options) as driver, driver.connection() as conn:
        async_rows = await conn.fetch_all(sql, params)
    reference = await sync_rows(case, options, sql, params)
    return RowsComparison(normalise(async_rows), normalise(reference))


ROUND_TRIP_SQL = (
    "SELECT %s::numeric, %s::timestamptz, %s::date, %s::time, %s::interval, %s::uuid, "
    "%s::bytea, %s::text, %s::boolean, %s::int"
)


def round_trip_params(case: TzCase) -> tuple[Any, ...]:
    moment = datetime(2026, 11, 24, 12, 30, 15, 123456)
    return (
        Decimal("-1234.500000"),
        moment.replace(tzinfo=UTC) if case.use_tz else moment,
        date(2026, 11, 24),
        time(23, 59, 59, 999999),
        timedelta(days=1, microseconds=5),
        uuid.UUID(int=2**100 + 7),
        b"\x00\xff\x10",
        "한글 ✓",
        True,
        None,
    )


async def compare_round_trip(case: TzCase, options: dict[str, Any] | None) -> RowsComparison:
    return await compare_with_sync(
        ROUND_TRIP_SQL, round_trip_params(case), case=case, options=options
    )


# --------------------------------------------------------------------------- recording driver
async def perform_operation(conn: AsyncDbConnection, operation: str) -> Any:
    """Call one connection method by name with canned arguments."""
    calls = {
        "fetch_all": lambda: conn.fetch_all("SELECT 1", (1,)),
        "execute": lambda: conn.execute("UPDATE t SET a = 1", None),
        "execute_many": lambda: conn.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,)]),
        "stream": lambda: stream_chunks(conn, "SELECT 1", None, 2),
        "begin": conn.begin,
        "commit": conn.commit,
        "rollback": conn.rollback,
        "savepoint": lambda: conn.savepoint("sp_1"),
        "release_savepoint": lambda: conn.release_savepoint("sp_1"),
        "rollback_to_savepoint": lambda: conn.rollback_to_savepoint("sp_1"),
    }
    return await calls[operation]()


def statement_kinds(driver: Any) -> list[str]:
    return [s.kind for s in driver.statements]


async def run_on_two_connections(driver: Any) -> None:
    async with driver.connection() as first:
        await first.begin()
    async with driver.connection() as second:
        await second.commit()


async def queue_error_then_rows(driver: Any, error: BaseException) -> list[Any]:
    """Queue an error followed by rows; return [error raised by 1st call, rows of 2nd call]."""
    driver.queue(error, [(1,)])
    outcome: list[Any] = []
    async with driver.connection() as conn:
        try:
            await conn.fetch_all("SELECT 1", None)
        except BaseException as exc:
            outcome.append(exc)
        outcome.append(await conn.fetch_all("SELECT 1", None))
    return outcome


# --------------------------------------------------------------------------- round 2: aftermath
async def settled_stats(driver: Any) -> PoolStats:
    """Pool counters once no connection is in use and nobody waits."""

    async def idle() -> bool:
        stats = driver.pool_stats()
        return stats.in_use == 0 and stats.waiting == 0

    await wait_until(idle)
    return driver.pool_stats()


async def reuse_error(conn: AsyncDbConnection) -> BaseException | None:
    """The exception raised by using `conn` again (None when it still works)."""
    try:
        await conn.fetch_all("SELECT 1", None)
    except Exception as exc:
        return exc
    return None


async def pid_ends(pid: int) -> bool:
    try:
        await wait_backend_gone(pid)
    except TimeoutError:
        return False
    return True


async def isolation_in_transaction(options: dict[str, Any]) -> str:
    async with open_driver(options=options) as driver, driver.connection() as conn:
        await conn.begin()
        level = await session_setting(conn, "transaction_isolation")
        await conn.rollback()
    return level


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


@dataclass(frozen=True)
class FailingStream:
    error: BaseException | None
    rows_seen: int
    cursors_after: list[CursorRow]


FAILING_STREAM_SQL = "SELECT 10 / (5 - g) FROM generate_series(1, 10) g ORDER BY g"


async def consume_failing_stream(conn: AsyncDbConnection, *, transactional: bool) -> FailingStream:
    """Drain a stream whose query divides by zero partway through."""
    error: BaseException | None = None
    seen = 0
    if transactional:
        await conn.begin()
    try:
        async with conn.stream(FAILING_STREAM_SQL, None, 2) as chunks:
            async for chunk in chunks:
                seen += len(chunk)
    except Exception as exc:
        error = exc
    if transactional:
        await conn.rollback()
    return FailingStream(error, seen, await open_cursors(conn))


@dataclass(frozen=True)
class StreamCancelOutcome:
    cancelled: bool
    reuse_error: BaseException | None
    old_backend_ended: bool
    fresh_cursors: list[CursorRow]
    after: PoolStats


async def cancel_open_stream(driver: Any, *, transactional: bool) -> StreamCancelOutcome:
    """Cancel a task that sits inside an open stream (after its first chunk)."""
    held: dict[str, Any] = {}
    ready = asyncio.Event()

    async def worker() -> None:
        async with driver.connection() as conn:
            held["conn"] = conn
            held["pid"] = await backend_pid(conn)
            if transactional:
                await conn.begin()
            async with conn.stream("SELECT g FROM generate_series(1, 100) g", None, 2) as chunks:
                await anext(chunks)
                ready.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    async with asyncio.timeout(10):
        await ready.wait()
    task.cancel()
    await asyncio.wait([task])
    error = await reuse_error(held["conn"])
    ended = await pid_ends(held["pid"])
    after = await settled_stats(driver)
    async with driver.connection() as fresh:
        cursors = await open_cursors(fresh)
    return StreamCancelOutcome(task.cancelled(), error, ended, cursors, after)


@dataclass(frozen=True)
class WriteCancelOutcome:
    cancelled: bool
    reuse_error: BaseException | None
    rows_left: int
    old_backend_ended: bool
    fresh_pid: int
    old_pid: int
    after: PoolStats


async def cancel_write_in_transaction(driver: Any, table: str) -> WriteCancelOutcome:
    """Insert inside a transaction, then cancel while a later statement sleeps."""
    marker = f"probe_{uuid.uuid4().hex}"
    held: dict[str, Any] = {}

    async def worker() -> None:
        async with driver.connection() as conn:
            held["conn"] = conn
            held["pid"] = await backend_pid(conn)
            await conn.begin()
            await insert_named(conn, table, "a")
            await conn.fetch_all(f"SELECT pg_sleep(30) /* {marker} */", None)

    task = asyncio.create_task(worker())

    async def started() -> bool:
        return await _probe_active(marker)

    await wait_until(started)
    task.cancel()
    await asyncio.wait([task])
    error = await reuse_error(held["conn"])
    ended = await pid_ends(held["pid"])
    after = await settled_stats(driver)
    async with driver.connection() as fresh:
        fresh_pid = await backend_pid(fresh)
    return WriteCancelOutcome(
        task.cancelled(), error, await scratch_count(table), ended, fresh_pid, held["pid"], after
    )


async def cancel_after_own_backend_killed(driver: Any) -> bool:
    """Cancel a task whose connection died inside a transaction; True when it ended cancelled."""
    ready = asyncio.Event()

    async def worker() -> None:
        async with driver.connection() as conn:
            await conn.begin()
            pid = await backend_pid(conn)
            await terminate_backend(pid)
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    async with asyncio.timeout(10):
        await ready.wait()
    task.cancel()
    await asyncio.wait([task])
    return task.cancelled()


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
    import psycopg_pool

    return exc is not None and isinstance(exc.__cause__, psycopg_pool.PoolTimeout)


# --------------------------------------------------------------------------- Django drift
POPPED_OPTIONS = ("assume_role", "isolation_level", "pool", "server_side_binding")


def drift_settings(name: str | None, options: dict[str, Any] | None) -> dict[str, Any]:
    settings_dict = pg_settings(options=options)
    settings_dict["NAME"] = name
    return settings_dict


@dataclass(frozen=True)
class ConnKwargsComparison:
    ours: dict[str, Any]
    reference: dict[str, Any]

    def differences(self) -> dict[str, tuple[Any, Any]]:
        """Keys whose values differ.

        The cursor factory is compared by name; `autocommit` is ours alone (Django sets it in
        `get_new_connection`, we pass it to the pool's connections).
        """
        keys = (set(self.ours) | set(self.reference)) - {"cursor_factory", "autocommit"}
        return {
            k: (self.ours.get(k), self.reference.get(k))
            for k in sorted(keys)
            if self.ours.get(k) != self.reference.get(k)
        }

    def cursor_factories_correspond(self) -> bool:
        """Django's factory is `CursorMixin` + the sync psycopg class we mirror asynchronously."""
        ours = self.ours["cursor_factory"].__name__.removeprefix("Async")
        return ours in [c.__name__ for c in self.reference["cursor_factory"].__mro__]


def compare_conn_kwargs(
    name: str | None, options: dict[str, Any] | None, case: TzCase = UTC_CASE
) -> ConnKwargsComparison:
    from asyncopation.drivers.postgresql.settings import build_config

    settings_dict = drift_settings(name, options)
    ours = build_config(
        "default", settings_dict, use_tz=case.use_tz, time_zone=case.settings_time_zone
    ).conn_kwargs
    with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
        wrapper = ported("DatabaseWrapper")(copy.deepcopy(settings_dict), "default")
        reference = wrapper.get_connection_params()
    return ConnKwargsComparison(ours, reference)


def leftover_option_keys(options: dict[str, Any]) -> list[str]:
    from asyncopation.drivers.postgresql.settings import build_config

    kwargs = build_config(
        "default", drift_settings("db", options), use_tz=True, time_zone="UTC"
    ).conn_kwargs
    return [k for k in POPPED_OPTIONS if k in kwargs]


def timezone_pair(case: TzCase) -> tuple[Any, Any]:
    """(our `(tzinfo, name)`, Django's `(wrapper.timezone, wrapper.timezone_name)`)."""
    from asyncopation.drivers.postgresql.settings import resolve_timezone

    settings_dict = drift_settings("db", None)
    settings_dict["TIME_ZONE"] = case.db_time_zone
    ours = resolve_timezone(settings_dict, use_tz=case.use_tz, time_zone=case.settings_time_zone)
    with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
        wrapper = ported("DatabaseWrapper")(settings_dict, "default")
        return ours, (wrapper.timezone, wrapper.timezone_name)


def quote_pair(name: str) -> tuple[str, str]:
    from asyncopation.drivers.postgresql.connection import _quote

    return _quote(name), ported("DatabaseOperations")(None).quote_name(name)


def cursor_base_names() -> tuple[list[str], list[str]]:
    """(our stream cursor bases, Django's `ServerSideCursor` bases) without `Async`/`CursorMixin`."""
    from asyncopation.drivers.postgresql.connection import AsyncClientServerCursor

    ours = [c.__name__.removeprefix("Async") for c in AsyncClientServerCursor.__bases__]
    theirs = [
        c.__name__ for c in ported("ServerSideCursor").__bases__ if c.__name__ != "CursorMixin"
    ]
    return ours, theirs


def pool_kwargs_for(settings_overrides: dict[str, Any], pool: dict[str, Any] | None) -> dict:
    from asyncopation.drivers.postgresql.settings import build_config

    settings_dict = drift_settings("db", {"pool": pool} if pool is not None else None)
    settings_dict.update(settings_overrides)
    return build_config("default", settings_dict, use_tz=True, time_zone="UTC").pool_kwargs


def timezone_name_for(case: TzCase) -> str:
    from asyncopation.drivers.postgresql.settings import build_config

    settings_dict = drift_settings("db", None)
    settings_dict["TIME_ZONE"] = case.db_time_zone
    config = build_config(
        "default", settings_dict, use_tz=case.use_tz, time_zone=case.settings_time_zone
    )
    return config.timezone_name
