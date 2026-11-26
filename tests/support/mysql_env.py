"""Vocabulary, settings, driver factories and the admin connection for MySQL/MariaDB driver tests.

The asyncmy driver is imported lazily so that modules which only need the vocabulary import
without it. The admin connection is a plain asyncmy autocommit connection of the test user.
"""

from __future__ import annotations

import copy
import time
import uuid
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from typing import Any
from unittest import mock

import pytest
from django.db import connections

from asyncopation.drivers.base import PoolStats
from harness.settings import VENDOR
from tests.support.aio import wait_until

MYSQL_FAMILY = pytest.mark.skipif(
    VENDOR not in ("mysql", "mariadb"), reason="MySQL/MariaDB driver tests"
)
MARIADB_ONLY = pytest.mark.skipif(VENDOR != "mariadb", reason="MariaDB only")
MYSQL_ONLY = pytest.mark.skipif(VENDOR != "mysql", reason="MySQL only")

RUN_ID = uuid.uuid4().hex[:8]  # global DB object names carry it: concurrent checkouts share a DB
SCRATCH_TABLE = f"asyncopation_scratch_{RUN_ID}"
# Check constraint names are unique per schema in MySQL: the name carries the run id too.
SCRATCH_DDL = (
    "id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(64) NOT NULL UNIQUE, "
    f"qty INT NOT NULL DEFAULT 0, CONSTRAINT qty_not_negative_{RUN_ID} CHECK (qty >= 0)"
)
SLEEP_SECONDS = 30

type Row = tuple[Any, ...]


# --------------------------------------------------------------------------- settings, drivers
def my_settings(
    *,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Django DATABASES entry for the test database, with OPTIONS merged.

    Pool options live under the top-level ``ASYNCOPATION`` key (ADR-0001 Decision 8), never in
    ``OPTIONS``: Django forwards every ``OPTIONS`` key to ``MySQLdb.connect``.
    """
    settings_dict = copy.deepcopy(dict(connections["default"].settings_dict))
    settings_dict["OPTIONS"] = {**settings_dict.get("OPTIONS", {}), **(options or {})}
    if pool is not None:
        settings_dict["ASYNCOPATION"] = {"pool": pool}
    settings_dict.update(overrides or {})
    return settings_dict


def build_my_driver(
    *,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Any:
    """A `MySQLDriver` that is not opened yet."""
    from asyncopation.drivers.mysql import MySQLDriver

    settings_dict = my_settings(options=options, pool=pool, overrides=overrides)
    return MySQLDriver.from_settings("default", settings_dict, use_tz=True, time_zone="UTC")


@asynccontextmanager
async def open_my_driver(
    *,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> AsyncGenerator[Any]:
    driver = build_my_driver(options=options, pool=pool, overrides=overrides)
    await driver.open()
    try:
        yield driver
    finally:
        await driver.close()


# --------------------------------------------------------------------------- admin connection
async def admin_script(statements: Sequence[str], params: Sequence[Any] | None = None) -> list[Row]:
    """Run statements in order on ONE separate autocommit connection; rows of the last one.

    `params` go to the last statement only. A statement without a result set yields `[]`.
    """
    import asyncmy

    cfg = connections["default"].settings_dict
    conn = await asyncmy.connect(
        host=cfg["HOST"],
        port=int(cfg["PORT"]),
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        database=cfg["NAME"],
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        async with conn.cursor() as cursor:
            # A lock a regressed driver left behind must turn into a red test, not into a DROP
            # that waits for a year (the server default of `lock_wait_timeout`).
            await cursor.execute("SET SESSION lock_wait_timeout = 5")
            for statement in statements[:-1]:
                await cursor.execute(statement)
            await cursor.execute(statements[-1], params)
            return [tuple(row) for row in await cursor.fetchall()]
    finally:
        await conn.ensure_closed()


async def admin_query(sql: str, params: Sequence[Any] | None = None) -> list[Row]:
    return await admin_script([sql], params)


async def admin_scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    return (await admin_query(sql, params))[0][0]


async def create_scratch_table() -> str:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
    await admin_query(f"CREATE TABLE {SCRATCH_TABLE} ({SCRATCH_DDL}) ENGINE=InnoDB")
    return SCRATCH_TABLE


async def drop_scratch_table() -> None:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")


async def scratch_count(table: str = SCRATCH_TABLE) -> int:
    return await admin_scalar(f"SELECT COUNT(*) FROM {table}")


async def scratch_names(table: str = SCRATCH_TABLE) -> list[str]:
    return [row[0] for row in await admin_query(f"SELECT name FROM {table} ORDER BY id")]


async def scratch_rows(conn: Any, table: str = SCRATCH_TABLE) -> list[Row]:
    return await conn.fetch_all(f"SELECT name, qty FROM {table} ORDER BY id", None)


async def insert_named(conn: Any, table: str, name: str, qty: int = 0) -> None:
    await conn.execute(f"INSERT INTO {table} (name, qty) VALUES (%s, %s)", (name, qty))


# --------------------------------------------------------------------------- server facts
async def server_scalar(sql: str) -> Any:
    """First column of the first row of `sql`, from the admin connection."""
    return await admin_scalar(sql)


@contextmanager
def tracked_pool_threads() -> Generator[list[int]]:
    """Server thread ids of every connection asyncmy's pool opens while active.

    Counting by id keeps leak checks per run: other connections on the shared server (other
    tests, other checkouts) are never part of the count.
    """
    import asyncmy.pool

    pool_module: Any = asyncmy.pool
    real = pool_module.connect
    seen: list[int] = []

    async def recording(**kwargs: Any) -> Any:
        conn = await real(**kwargs)
        seen.append(conn.thread_id())
        return conn

    with mock.patch.object(pool_module, "connect", recording):
        yield seen


async def live_thread_count(thread_ids: Sequence[int]) -> int:
    """How many of `thread_ids` still exist on the server."""
    if not thread_ids:
        return 0
    listed = ", ".join(str(int(i)) for i in thread_ids)
    table = "performance_schema" if VENDOR == "mysql" else "information_schema"
    return await admin_scalar(f"SELECT COUNT(*) FROM {table}.processlist WHERE id IN ({listed})")


async def live_threads_settle_to(
    thread_ids: Sequence[int], expected: int, *, within: float = 10.0
) -> int:
    """`live_thread_count` once it equals `expected` (the last value when that never happens)."""

    async def settled() -> bool:
        return await live_thread_count(thread_ids) == expected

    with suppress(TimeoutError):
        await wait_until(settled, within=within)
    return await live_thread_count(thread_ids)


async def connection_id(conn: Any) -> int:
    return (await conn.fetch_all("SELECT CONNECTION_ID()", None))[0][0]


async def thread_exists(thread_id: int) -> bool:
    if VENDOR == "mysql":
        sql = "SELECT COUNT(*) FROM performance_schema.processlist WHERE id = %s"
    else:
        sql = "SELECT COUNT(*) FROM information_schema.processlist WHERE id = %s"
    return bool(await admin_scalar(sql, (thread_id,)))


async def statement_running(marker: str) -> bool:
    """True while a statement containing `marker` runs on the server (the admin query that looks
    for it carries the marker too and is excluded)."""
    if VENDOR == "mysql":
        sql = "SELECT COUNT(*) FROM performance_schema.processlist WHERE info LIKE %s AND id <> CONNECTION_ID()"
    else:
        sql = "SELECT COUNT(*) FROM information_schema.processlist WHERE info LIKE %s AND id <> CONNECTION_ID()"
    return bool(await admin_scalar(sql, (f"%{marker}%",)))


async def thread_ends(thread_id: int, *, within: float = 5.0) -> bool:
    """True once the server thread is gone, False when it is still there after `within` seconds."""

    async def gone() -> bool:
        return not await thread_exists(thread_id)

    try:
        await wait_until(gone, within=within)
    except TimeoutError:
        return False
    return True


async def kill_thread(thread_id: int) -> bool:
    await admin_query(f"KILL {int(thread_id)}")
    return await thread_ends(thread_id)


async def settled_stats(driver: Any, *, min_idle: int = 0, within: float = 5.0) -> PoolStats:
    """Pool counters once nothing is in use or waiting and `min_idle` connections are idle.

    Returns the current counters when that does not happen within `within` seconds.
    """

    async def idle() -> bool:
        stats = driver.pool_stats()
        return stats.in_use == 0 and stats.waiting == 0 and stats.idle >= min_idle

    with suppress(TimeoutError):
        await wait_until(idle, within=within)
    return driver.pool_stats()


@dataclass(frozen=True)
class Timed[T]:
    value: T | None
    error: BaseException | None
    elapsed: float


async def timed(coro: Any, *, limit: float = 8.0) -> Timed[Any]:
    """Await `coro` with a safety limit; capture its value or exception and the elapsed time."""
    import asyncio

    started = time.monotonic()
    try:
        async with asyncio.timeout(limit):
            value = await coro
    except Exception as exc:
        return Timed(None, exc, time.monotonic() - started)
    return Timed(value, None, time.monotonic() - started)


@contextmanager
def tracked_direct_connections() -> Generator[list[Any]]:
    """Every connection made through `asyncmy.connect` (the driver's probe) while active."""
    import asyncmy

    real = asyncmy.connect
    seen: list[Any] = []

    async def recording(**kwargs: Any) -> Any:
        conn = await real(**kwargs)
        seen.append(conn)
        return conn

    with mock.patch.object(asyncmy, "connect", recording):
        yield seen


def installed_asyncmy_in_range() -> bool:
    """The installed asyncmy satisfies the `asyncmy>=0.2.18,<0.3` pin (pyproject)."""
    from importlib.metadata import version

    from packaging.version import Version

    return Version("0.2.18") <= Version(version("asyncmy")) < Version("0.3")
