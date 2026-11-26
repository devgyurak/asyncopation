"""Driver micro-benchmarks: ``MySQLDriver`` vs the raw asyncmy pool calls it wraps.

The MySQL/MariaDB twin of ``bench/driver_micro.py`` (same method and metrics). Cases are named
``mysqldriver.<group>.<impl>.t<tasks>``:

* groups: ``select1`` (``SELECT 1``), ``wide`` (one row of 12 mixed-type columns), ``execute``
  (``INSERT`` with rowcount and lastrowid on a scratch table), ``tx`` (begin / statement /
  commit), ``stream`` (200 rows of a recursive CTE in chunks of 50) and ``noio`` (``SELECT 1``
  against fake pool/connection/cursor objects: a LOWER BOUND of the driver's own cost, 1 task)
* impls: ``raw`` (``pool.acquire()`` + cursor + ``release()``, with the rollback a careful caller
  needs when a transaction is left open), ``getconn`` (acquire, the same statement, release: the
  cheapest baseline) and ``driver`` (``MySQLDriver.connection()`` + the driver API)
* tasks: 1, 8 and 64 concurrent workers (``noio``: 1)

The raw baselines use their own pool built from the driver's configuration (same sizes and
connection arguments, including the decoder shim). Each round runs every impl for ``seconds``
(interleaved, rotated every round); the stable metric is client CPU per operation and the paired
ratios ``driver / raw`` and ``driver / getconn`` are per-round (see ``driver_micro``).
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from typing import Any

import asyncmy
from asyncmy.cursors import SSCursor

from bench.driver_micro import SINGLE_TASK_GROUPS, TASKS, measure_group

type Op = Callable[[], Awaitable[Any]]
type Body = Callable[[Any], Awaitable[Any]]

PREFIX = "mysqldriver"
SELECT1 = "SELECT 1"
WIDE = (
    "SELECT 1 a, 2 b, 'text value' c, NOW() d, CAST(12.34 AS DECIMAL(10, 2)) e, "
    "JSON_OBJECT('a', 1) f, CAST('ab' AS BINARY) g, DATE '2024-01-01' h, TIME '12:00:00' i, "
    "1.5e0 j, NULL k, CAST(7 AS SIGNED) l"
)
STREAM = (
    "WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 200) SELECT n FROM s"
)
STREAM_CHUNK = 50
SCRATCH = "bench_driver_scratch"
INSERT = f"INSERT INTO {SCRATCH} (v) VALUES (%s)"
GROUPS = {
    "select1": ("raw", "getconn", "driver"),
    "wide": ("raw", "getconn", "driver"),
    "execute": ("raw", "getconn", "driver"),
    "tx": ("raw", "getconn", "driver"),
    "stream": ("raw", "getconn", "driver"),
    "noio": ("getconn", "driver"),
}


def case_names() -> list[str]:
    return [
        f"{PREFIX}.{g}.{i}.t{t}"
        for t in TASKS
        for g, impls in GROUPS.items()
        if t == 1 or g not in SINGLE_TASK_GROUPS
        for i in impls
    ]


def known_suffixes() -> set[str]:
    return {n.split(".", 1)[1] for n in case_names()}


def selected(only: set[str]) -> list[str]:
    """Selected cases plus the baselines of their group, so ratios can always be computed."""
    chosen = {n for n in case_names() if not only or n.split(".", 1)[1] in only}
    wanted = set(chosen)
    for name in chosen:
        _, group, _, tasks = name.split(".")
        wanted |= {f"{PREFIX}.{group}.{i}.{tasks}" for i in GROUPS[group] if i != "driver"}
    return [n for n in case_names() if n in wanted]


# --------------------------------------------------------------------------- fakes (noio group)
class _FakeCursor:
    description = (("1",),)
    rowcount = 1
    lastrowid = 0

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        return None

    async def fetchall(self) -> tuple[tuple[int], ...]:
        return ((1,),)


class _FakeRaw:
    connected = True

    def cursor(self, *cursor_class: Any) -> _FakeCursor:
        return _FakeCursor()

    def get_transaction_status(self) -> bool:
        return False


class _FakePool:
    def __init__(self) -> None:
        self._raw = _FakeRaw()

    async def acquire(self) -> _FakeRaw:
        return self._raw

    async def release(self, raw: _FakeRaw) -> None:
        return None


# --------------------------------------------------------------------------- operations
def _raw_bodies() -> dict[str, Body]:
    """Raw-asyncmy bodies working on one borrowed connection (shared by raw and getconn)."""
    values = itertools.count()

    def query(sql: str) -> Body:
        async def body(conn: Any) -> Any:
            async with conn.cursor() as cursor:
                await cursor.execute(sql)
                return await cursor.fetchall()

        return body

    async def execute(conn: Any) -> Any:
        async with conn.cursor() as cursor:
            await cursor.execute(INSERT, (next(values),))
            return cursor.rowcount, cursor.lastrowid

    async def tx(conn: Any) -> Any:
        await conn.begin()
        async with conn.cursor() as cursor:
            await cursor.execute(SELECT1)
            rows = await cursor.fetchall()
        await conn.commit()
        return rows

    async def stream(conn: Any) -> Any:
        cursor = conn.cursor(SSCursor)
        try:
            await cursor.execute(STREAM)
            total = 0
            while rows := await cursor.fetchmany(STREAM_CHUNK):
                total += len(rows)
            return total
        finally:
            await cursor.close()

    return {
        "select1": query(SELECT1),
        "wide": query(WIDE),
        "execute": execute,
        "tx": tx,
        "stream": stream,
    }


def _borrowing(pool: Any, body: Body, *, careful: bool) -> Op:
    """``careful``: end a transaction left open before release (what the driver's reset does)."""
    if not careful:

        async def via_getconn() -> Any:
            conn = await pool.acquire()
            try:
                return await body(conn)
            finally:
                await pool.release(conn)

        return via_getconn

    async def via_raw() -> Any:
        conn = await pool.acquire()
        try:
            return await body(conn)
        finally:
            try:
                if conn.get_transaction_status():
                    await conn.rollback()
            finally:
                await pool.release(conn)

    return via_raw


def make_ops(driver: Any, pool: Any) -> dict[str, dict[str, Op]]:
    """The operations of every group, keyed by impl."""
    values = itertools.count()

    def driver_query(sql: str) -> Op:
        async def op() -> Any:
            async with driver.connection() as conn:
                return await conn.fetch_all(sql, None)

        return op

    async def driver_execute() -> Any:
        async with driver.connection() as conn:
            return await conn.execute(INSERT, (next(values),))

    async def driver_tx() -> Any:
        async with driver.connection() as conn:
            await conn.begin()
            rows = await conn.fetch_all(SELECT1, None)
            await conn.commit()
            return rows

    async def driver_stream() -> Any:
        async with driver.connection() as conn, conn.stream(STREAM, None, STREAM_CHUNK) as chunks:
            return sum([len(rows) async for rows in chunks])

    bodies = _raw_bodies()
    ops: dict[str, dict[str, Op]] = {
        group: {
            "raw": _borrowing(pool, body, careful=True),
            "getconn": _borrowing(pool, body, careful=False),
        }
        for group, body in bodies.items()
    }
    ops["select1"]["driver"] = driver_query(SELECT1)
    ops["wide"]["driver"] = driver_query(WIDE)
    ops["execute"]["driver"] = driver_execute
    ops["tx"]["driver"] = driver_tx
    ops["stream"]["driver"] = driver_stream
    return ops


def make_noio_ops(config: Any) -> dict[str, Op]:
    """``getconn`` and ``driver`` over a fake pool: no network, so the difference is the driver."""
    from asyncopation.drivers.mysql.driver import MySQLDriver

    fake = _FakePool()
    fake_driver = MySQLDriver(config)
    fake_driver._pool = fake  # the driver borrows from whatever pool it holds

    async def via_driver() -> Any:
        async with fake_driver.connection() as conn:
            return await conn.fetch_all(SELECT1, None)

    async def via_getconn() -> Any:
        raw = await fake.acquire()
        try:
            async with raw.cursor() as cursor:
                await cursor.execute(SELECT1)
                return await cursor.fetchall()
        finally:
            await fake.release(raw)

    return {"getconn": via_getconn, "driver": via_driver}


async def _scratch_table(pool: Any, *, create: bool) -> None:
    raw = await pool.acquire()
    try:
        async with raw.cursor() as cursor:
            await cursor.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
            if create:
                await cursor.execute(
                    f"CREATE TABLE {SCRATCH} (id BIGINT AUTO_INCREMENT PRIMARY KEY, v BIGINT)"
                )
    finally:
        await pool.release(raw)


async def run(
    driver: Any, config: Any, names: list[str], seconds: float, rounds: int
) -> dict[str, Any]:
    """Run the selected ``mysqldriver.*`` cases; ``config`` is the driver's ``MySQLConfig``."""
    results: dict[str, Any] = {}
    pool = None
    try:
        await driver.open()
        # Same arguments as the driver's own pool (its server data decides `init_command`).
        pool = await asyncmy.create_pool(
            **config.pool_kwargs,
            **config.pooled_kwargs(sql_auto_is_null=driver.server_data.sql_auto_is_null),
        )
        await _scratch_table(pool, create=True)
        ops = make_ops(driver, pool)
        ops["noio"] = make_noio_ops(config)
        for tasks in TASKS:
            for group, impls in GROUPS.items():
                wanted = [i for i in impls if f"{PREFIX}.{group}.{i}.t{tasks}" in names]
                if wanted:
                    results |= await measure_group(
                        group, tasks, wanted, ops[group], seconds, rounds, PREFIX
                    )
    finally:
        try:
            if pool is not None:
                await _scratch_table(pool, create=False)
        finally:
            if pool is not None:
                pool.close()
                await pool.wait_closed()
            await driver.close()
    return results
