"""Observations on a borrowed connection: backend state, cursors, streams, released use."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from asyncopation.drivers.base import AsyncDbConnection, AsyncDriver
from tests.support.aio import collect
from tests.support.drivers.admin import SCRATCH_TABLE, Row


# --------------------------------------------------------------------------- static conformance
def as_driver(driver: AsyncDriver) -> AsyncDriver:
    """Static check: the argument must satisfy the `AsyncDriver` protocol."""
    return driver


def as_connection(connection: AsyncDbConnection) -> AsyncDbConnection:
    """Static check: the argument must satisfy the `AsyncDbConnection` protocol."""
    return connection


def set_attribute(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


# --------------------------------------------------------------------------- session probes
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


async def reuse_error(conn: AsyncDbConnection) -> BaseException | None:
    """The exception raised by using `conn` again (None when it still works)."""
    try:
        await conn.fetch_all("SELECT 1", None)
    except Exception as exc:
        return exc
    return None


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


def holdable_flags(cursors: Iterable[CursorRow]) -> list[bool]:
    return [c.is_holdable for c in cursors]


def scrollable_flags(cursors: Iterable[CursorRow]) -> list[bool]:
    return [c.is_scrollable for c in cursors]


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


FAILING_STREAM_SQL = "SELECT 10 / (5 - g) FROM generate_series(1, 10) g"


@dataclass(frozen=True)
class FailingStream:
    error: BaseException | None
    rows_seen: int
    cursors_after: list[CursorRow]


async def consume_failing_stream(conn: AsyncDbConnection, *, transactional: bool) -> FailingStream:
    """Drain a stream whose query divides by zero at the fifth row (chunk size 2)."""
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


# --------------------------------------------------------------------------- operations by name
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


async def error_of_operation(conn: AsyncDbConnection, operation: str) -> BaseException | None:
    try:
        await perform_operation(conn, operation)
    except Exception as exc:
        return exc
    return None


# --------------------------------------------------------------------------- recording driver
def statement_kinds(driver: Any) -> list[str]:
    return [s.kind for s in driver.statements]


async def run_on_two_connections(driver: Any) -> None:
    async with driver.connection() as first:
        await first.begin()
        await first.commit()
    async with driver.connection() as second:
        await second.begin()
        await second.rollback()


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


async def error_of_call(driver: Any, operation: str) -> BaseException | None:
    """The exception a connection call raises (None when it returns normally)."""
    async with driver.connection() as conn:
        return await error_of_operation(conn, operation)


def logged_params(driver: Any, index: int = 0) -> Any:
    """The params of the `index`-th logged statement as a plain list or dict."""
    params = driver.statements[index].params
    return dict(params) if hasattr(params, "keys") else list(params)


async def kinds_after_leaving(driver: Any, *operations: str) -> list[str]:
    """Statement kinds logged after the given operations ran and the connection was released."""
    async with driver.connection() as conn:
        for operation in operations:
            await perform_operation(conn, operation)
    return statement_kinds(driver)


async def next_rows_after_commit(driver: Any, operation: str) -> list[Row]:
    """Call `operation` (commit/rollback), then fetch: rows prove the queue was left untouched."""
    async with driver.connection() as conn:
        await perform_operation(conn, operation)
        return await conn.fetch_all("SELECT 1", None)
