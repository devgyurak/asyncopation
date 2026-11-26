"""A fake asyncmy pool and raw connections to drive `MySQLDriver.connection()` without a server."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.mysql_driver_drift import offline_my_driver


class FakeRaw:
    """The corner of an asyncmy connection that `connection()` touches."""

    def __init__(self, ping_error: BaseException | None = None) -> None:
        self.connected = True
        self.closed = False
        self.ping_calls: list[bool] = []
        self._ping_error = ping_error

    async def ping(self, reconnect: bool = True) -> None:
        self.ping_calls.append(reconnect)
        if self._ping_error is not None:
            raise self._ping_error

    async def ensure_closed(self) -> None:
        self.closed = True
        self.connected = False

    def get_transaction_status(self) -> bool:
        return False


class FakePool:
    def __init__(self, raws: list[FakeRaw], acquire_error: BaseException | None = None) -> None:
        self._raws = list(raws)
        self._acquire_error = acquire_error
        self.released: list[FakeRaw] = []
        self.size = len(raws)
        self.freesize = len(raws)

    async def acquire(self) -> FakeRaw:
        if self._acquire_error is not None:
            raise self._acquire_error
        return self._raws.pop(0)

    async def release(self, raw: FakeRaw) -> None:
        self.released.append(raw)


def driver_with_fake_pool(pool: FakePool, *, health_checks: bool) -> Any:
    driver = offline_my_driver(None, CONN_HEALTH_CHECKS=health_checks)
    driver._pool = pool
    return driver


def lost_connection() -> BaseException:
    import asyncmy.errors

    return asyncmy.errors.OperationalError(2013, "Lost connection to MySQL server during query")


@dataclass(frozen=True)
class FakeBorrow:
    served: FakeRaw
    bad: FakeRaw
    good: FakeRaw
    pool: FakePool


async def borrow_past_a_dead_idle_connection(*, health_checks: bool) -> FakeBorrow:
    """The pool hands out a dead connection first, then a good one."""
    bad, good = FakeRaw(ping_error=lost_connection()), FakeRaw()
    pool = FakePool([bad, good])
    driver = driver_with_fake_pool(pool, health_checks=health_checks)
    async with driver.connection() as conn:
        served = conn._raw
    return FakeBorrow(served, bad, good, pool)


@dataclass(frozen=True)
class CancelledPing:
    cancelled: bool
    error: BaseException | None
    raw: FakeRaw
    pool: FakePool


async def borrow_with_a_cancelled_ping() -> CancelledPing:
    """The task is cancelled while the health check pings."""
    raw = FakeRaw(ping_error=asyncio.CancelledError())
    pool = FakePool([raw])
    driver = driver_with_fake_pool(pool, health_checks=True)
    task = asyncio.create_task(_borrow(driver))
    await asyncio.wait([task])
    return CancelledPing(
        task.cancelled(), None if task.cancelled() else task.exception(), raw, pool
    )


async def _borrow(driver: Any) -> None:
    async with driver.connection():
        return None


async def acquire_error_when_the_pool_is_closing() -> BaseException | None:
    """`pool.acquire()` raises asyncmy's error for an acquire that raced `close()`."""
    pool = FakePool([], RuntimeError("Cannot acquire connection after closing pool"))
    driver = driver_with_fake_pool(pool, health_checks=False)
    try:
        await _borrow(driver)
    except Exception as exc:
        return exc
    return None


async def create_pool_kwargs(
    pool_options: dict[str, Any], *, sql_auto_is_null: bool = False
) -> dict[str, Any]:
    """The keyword arguments `open()` hands to `asyncmy.create_pool`, without any server."""
    from unittest import mock

    from asyncopation.drivers.mysql import MySQLDriver
    from tests.support.mysql_server import MYSQL_8_4, server_data

    seen: dict[str, Any] = {}

    async def create_pool(**kwargs: Any) -> FakePool:
        seen.update(kwargs)
        return FakePool([])

    driver = offline_my_driver(None, ASYNCOPATION={"pool": pool_options})
    with (
        mock.patch("asyncmy.create_pool", create_pool),
        mock.patch.object(
            MySQLDriver,
            "_probe",
            mock.AsyncMock(
                return_value=server_data({**MYSQL_8_4, "sql_auto_is_null": sql_auto_is_null})
            ),
        ),
    ):
        await driver.open()
    return seen


class FakeCursor:
    """A stream cursor whose `close()` is cancelled while it drains."""

    async def close(self) -> None:
        raise asyncio.CancelledError


async def open_cursors_after_a_cancelled_close() -> tuple[BaseException | None, list[Any], bool]:
    """(error, `_open_cursors`, raw discarded) after the drain of a stream cursor is cancelled."""
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = FakeRaw()
    conn = MySQLConnection(raw, server_side_cursors=True)  # ty: ignore[invalid-argument-type]
    cursor = FakeCursor()
    conn._open_cursors.append(cursor)
    error: BaseException | None = None
    try:
        await conn._close_cursor(cursor, quiet=False, abandoned=False, cancelled=False)
    except BaseException as exc:
        error = exc
    return error, list(conn._open_cursors), raw.closed


async def open_with_server_version(version: str) -> tuple[BaseException | None, bool, bool]:
    """`open()` with probe data whose `version` is `version`: (error, create_pool called, open)."""
    from unittest import mock

    from asyncopation.drivers.mysql import MySQLDriver
    from tests.support.mysql_server import MYSQL_8_4, server_data

    calls: list[Any] = []

    async def create_pool(**kwargs: Any) -> FakePool:
        calls.append(kwargs)
        return FakePool([])

    driver = offline_my_driver()
    data = server_data({**MYSQL_8_4, "version": version})
    error: BaseException | None = None
    with (
        mock.patch("asyncmy.create_pool", create_pool),
        mock.patch.object(MySQLDriver, "_probe", mock.AsyncMock(return_value=data)),
    ):
        try:
            await driver.open()
        except Exception as exc:
            error = exc
    return error, bool(calls), driver._pool is not None


# --------------------------------------------------------------------------- `_fail`
async def fail_with(exc: Exception) -> tuple[BaseException, bool]:
    """(the exception `MySQLConnection._fail(exc)` raises, whether it discarded the connection)."""
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = FakeRaw()
    conn = MySQLConnection(raw, server_side_cursors=False)  # ty: ignore[invalid-argument-type]
    try:
        await conn._fail(exc)
    except Exception as raised:
        return raised, raw.closed
    raise AssertionError("_fail must raise")


def asyncmy_error(name: str, *args: Any) -> Exception:
    import asyncmy.errors

    return getattr(asyncmy.errors, name)(*args)


# --------------------------------------------------------------------------- transaction state
class StateCursor:
    """Formats like a cursor; records nothing (client side formatting makes no round trip)."""

    def mogrify(self, sql: str, params: Any) -> str:
        return sql % (params if isinstance(params, dict) else tuple(params))


class StateRaw:
    """A raw connection recording the calls that carry transaction state."""

    _encoding = "utf8"

    def __init__(self) -> None:
        self.connected = True
        self.in_transaction_flag = False  # what the server's last status flags say
        self.calls: list[str] = []
        self.server_status = 0

    def get_transaction_status(self) -> bool:
        return self.in_transaction_flag

    def cursor(self, *_args: Any) -> Any:
        return StateCursor()

    async def begin(self) -> None:
        self.calls.append("begin")
        self.in_transaction_flag = True

    async def commit(self) -> None:
        self.calls.append("commit")
        self.in_transaction_flag = False

    async def rollback(self) -> None:
        self.calls.append("rollback")
        self.in_transaction_flag = False

    async def autocommit(self, value: bool) -> None:
        self.calls.append(f"autocommit({value})")

    async def query(self, sql: str) -> None:
        self.calls.append(f"query({sql})")

    async def ensure_closed(self) -> None:
        self.connected = False
        self.calls.append("closed")


def state_connection() -> tuple[Any, StateRaw]:
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = StateRaw()
    return MySQLConnection(raw, server_side_cursors=False), raw  # ty: ignore[invalid-argument-type]


@dataclass(frozen=True)
class StateOutcome:
    calls: list[str]
    autocommit_off: bool
    status_stale: bool


def _outcome(conn: Any, raw: StateRaw) -> StateOutcome:
    return StateOutcome(list(raw.calls), conn._autocommit_off, conn._status_stale)


async def resume_after(steps: list[str]) -> StateOutcome:
    """begin() (or not), optionally a server error, the server ending the transaction, then
    `_resume_transaction()` run `steps.count('resume')` times plus the named ending steps."""
    conn, raw = state_connection()
    for step in steps:
        if step == "begin":
            await conn.begin()
        elif step == "fail":
            await error_of_fail(conn)
        elif step == "server-ends":
            raw.in_transaction_flag = False
        elif step == "resume":
            await conn._resume_transaction()
        elif step == "commit":
            await conn.commit()
        elif step == "rollback":
            await conn.rollback()
        elif step == "reset":
            await conn.reset()
        elif step == "empty-batch":
            await conn.execute_many("INSERT INTO t (a) VALUES (%s)", [])
        elif step == "bad-count":
            await _bad_count_batch(conn)
        elif step.startswith("bad-set:"):
            await _bad_set_call(conn, step.split(":", 1)[1])
        elif step == "discard":
            await conn._discard()
        else:
            raise ValueError(f"unknown step {step!r}")
    return _outcome(conn, raw)


async def _bad_count_batch(conn: Any) -> None:
    """A batch whose placeholder count does not match: a client side ProgrammingError."""
    from django.db import ProgrammingError

    try:
        await conn.execute_many("SELECT %s, %s", [(1,)])
    except ProgrammingError:
        return None
    raise AssertionError("execute_many must raise ProgrammingError")


async def _bad_set_call(conn: Any, entry: str) -> None:
    """A set with non-string items through `entry`: a client side TypeError, no I/O."""
    try:
        if entry == "fetch_all":
            await conn.fetch_all("SELECT %s", ({1},))
        elif entry == "execute":
            await conn.execute("SELECT %s", ({1},))
        elif entry == "execute_many":
            await conn.execute_many("SELECT %s", [({1},)])
        else:
            async with conn.stream("SELECT %s", ({1},), 1):
                pass
    except TypeError:
        return None
    raise AssertionError(f"{entry} must raise TypeError")


async def error_of_fail(conn: Any) -> None:
    try:
        await conn._fail(asyncmy_error("IntegrityError", 1062, "dup"))
    except Exception:
        return None
    raise AssertionError("_fail must raise")


class FailingRaw(StateRaw):
    """The named raw operation raises `error`; the connection stays open like after a server error."""

    def __init__(self, operation: str, error: Exception) -> None:
        super().__init__()
        self._operation = operation
        self._error = error

    async def commit(self) -> None:
        await self._maybe_fail("commit")
        await super().commit()

    async def rollback(self) -> None:
        await self._maybe_fail("rollback")
        await super().rollback()

    async def autocommit(self, value: bool) -> None:
        if value:
            await self._maybe_fail("autocommit")
        await super().autocommit(value)

    async def _maybe_fail(self, operation: str) -> None:
        if operation == self._operation:
            raise self._error


async def end_transaction_with_failure(ending: str, operation: str) -> tuple[Exception, bool]:
    """Run `ending` ('commit' / 'rollback') with `operation` failing on a server error that
    leaves the link up; returns (raised exception, still connected)."""
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = FailingRaw(operation, asyncmy_error("OperationalError", 1317, "interrupted"))
    conn = MySQLConnection(raw, server_side_cursors=False)  # ty: ignore[invalid-argument-type]
    await conn.begin()
    raw.in_transaction_flag = False
    await conn._resume_transaction()  # autocommit is now off, so the restore has work to do
    try:
        await getattr(conn, ending)()
    except Exception as raised:
        return raised, raw.connected
    raise AssertionError(f"{ending}() must raise")


async def reset_with_failing_restore() -> tuple[bool, list[str]]:
    """reset() after a server-ended transaction whose `autocommit(True)` always fails:
    (connected afterwards, calls)."""
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = FailingRaw("autocommit", asyncmy_error("OperationalError", 1317, "interrupted"))
    conn = MySQLConnection(raw, server_side_cursors=False)  # ty: ignore[invalid-argument-type]
    await conn.begin()
    raw.in_transaction_flag = False
    await conn._resume_transaction()
    await conn.reset()
    return raw.connected, list(raw.calls)


class FlakyAutocommitRaw(StateRaw):
    """`autocommit(True)` fails the first time (the server went away, say)."""

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def autocommit(self, value: bool) -> None:
        if value and not self._failed:
            self._failed = True
            self.calls.append("autocommit(True) failed")
            raise asyncmy_error("OperationalError", 1290, "read-only")
        await super().autocommit(value)


async def restore_with_a_failing_first_attempt() -> tuple[Exception, bool, list[str]]:
    """commit() whose `autocommit(True)` fails with a server error: (the raised exception, still
    connected, calls)."""
    from asyncopation.drivers.mysql.connection import MySQLConnection

    raw = FlakyAutocommitRaw()
    conn = MySQLConnection(raw, server_side_cursors=False)  # ty: ignore[invalid-argument-type]
    await conn.begin()
    raw.in_transaction_flag = False
    await conn._resume_transaction()
    try:
        await conn.commit()
    except Exception as raised:
        return raised, raw.connected, list(raw.calls)
    raise AssertionError("commit() must raise when the autocommit restore fails")
