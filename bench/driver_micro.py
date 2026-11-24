"""Driver micro-benchmarks: ``PostgresDriver`` vs the raw psycopg_pool calls it wraps.

Cases are named ``driver.<group>.<impl>.t<tasks>``:

* groups: ``select1`` (``SELECT 1``), ``wide`` (one 15-column row), ``execute`` (``execute()`` with
  rowcount), ``tx`` (begin / statement / commit), ``stream`` (200 rows in chunks of 50) and
  ``noio`` (``SELECT 1`` against fake pool/connection/cursor objects: no network, no server) and
  ``noio-yield`` (the same, but the fake ``execute`` yields to the event loop once). The two ``noio``
  groups run with 1 task only and give a LOWER BOUND of the driver's own cost
* impls: ``raw`` (``pool.connection()`` + cursor), ``getconn`` (``getconn``/``putconn``: the
  cheapest possible baseline, present in every group) and ``driver``
* tasks: 1, 8 and 64 concurrent workers (``noio`` groups: 1)

Each round runs every impl of a group for ``seconds`` (interleaved; the order is rotated every
round). The stable metric is client CPU per operation. The report adds the per-round *paired*
ratios ``driver / raw`` and ``driver / getconn`` (median and inter-quartile range over the rounds,
paired by round index), the paired difference ``driver - getconn`` in microseconds and the load
average. Against a real server ratios within about +-5% are noise; the ``noio`` groups remove the
server and resolve the driver's own per-operation cost in microseconds (its IQR is shown for the ``noio`` groups).

The raw baselines use their own pool built from the driver's own configuration (same sizes and
connection kwargs). They do not run the driver's per-connection session setup (isolation level,
TimeZone, role), which costs nothing per operation. The ``execute`` and ``stream`` baselines use the
same idioms as the driver (no ``Column`` list per call; the client-binding named cursor).
"""

from __future__ import annotations

import asyncio
import itertools
import os
import statistics
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

type Op = Callable[[], Awaitable[Any]]
type Body = Callable[[Any], Awaitable[Any]]

TASKS = (1, 8, 64)
SELECT1 = "SELECT 1::int"
WIDE = (
    "SELECT 1::int a, 2::bigint b, 'text value'::text c, now() d, true e, 1.5::float8 f, "
    "12.34::numeric g, DATE '2024-01-01' h, TIME '12:00' i, gen_random_uuid() j, "
    "'{\"a\": 1}'::jsonb k, '\\x01'::bytea l, 'ab'::varchar(10) m, NULL::int n, 7::smallint o"
)
STREAM = "SELECT g FROM generate_series(1, 200) g"
STREAM_CHUNK = 50
GROUPS = {
    "select1": ("raw", "getconn", "driver"),
    "wide": ("raw", "getconn", "driver"),
    "execute": ("raw", "getconn", "driver"),
    "tx": ("raw", "getconn", "driver"),
    "stream": ("raw", "getconn", "driver"),
    "noio": ("getconn", "driver"),
    "noio-yield": ("getconn", "driver"),
}
SINGLE_TASK_GROUPS = frozenset({"noio", "noio-yield"})


def group_tasks(group: str) -> tuple[int, ...]:
    return (1,) if group in SINGLE_TASK_GROUPS else TASKS


def case_names() -> list[str]:
    return [
        f"driver.{g}.{i}.t{t}"
        for t in TASKS
        for g, impls in GROUPS.items()
        if t in group_tasks(g)
        for i in impls
    ]


def selected(only: set[str]) -> list[str]:
    """Selected cases plus the baselines of their group, so ratios can always be computed."""
    chosen = {n for n in case_names() if not only or n.split(".", 1)[1] in only}
    wanted = set(chosen)
    for name in chosen:
        _, group, _, tasks = name.split(".")
        wanted |= {f"driver.{group}.{impl}.{tasks}" for impl in GROUPS[group] if impl != "driver"}
    return [n for n in case_names() if n in wanted]


# --------------------------------------------------------------------------- fakes (noio group)
class _FakeResult:
    nfields = 1
    status = 2  # TUPLES_OK


class _FakeCursor:
    pgresult = _FakeResult()
    rowcount = 1

    def __init__(self, yielding: bool = False) -> None:
        self._yielding = yielding

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, sql: str, params: Any = None) -> None:
        if self._yielding:
            await asyncio.sleep(0)  # a real execute suspends the task at least once

    async def fetchall(self) -> list[tuple[int]]:
        return [(1,)]


class _FakeRaw:
    closed = False
    broken = False
    autocommit = True
    pgconn = SimpleNamespace(transaction_status=0)  # TransactionStatus.IDLE

    def __init__(self, yielding: bool = False) -> None:
        self._yielding = yielding

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._yielding)


class _FakePool:
    def __init__(self, yielding: bool = False) -> None:
        self._raw = _FakeRaw(yielding)

    async def getconn(self) -> _FakeRaw:
        return self._raw

    async def putconn(self, raw: _FakeRaw) -> None:
        return None


# --------------------------------------------------------------------------- operations
def _raw_bodies() -> dict[str, Body]:
    """Raw-psycopg bodies working on one borrowed raw connection (shared by raw and getconn)."""
    from asyncopation.drivers.postgresql.connection import AsyncClientServerCursor

    stream_ids = itertools.count(1)

    def query(sql: str) -> Body:
        async def body(conn: Any) -> Any:
            async with conn.cursor() as cursor:
                await cursor.execute(sql)
                return await cursor.fetchall()

        return body

    async def execute(conn: Any) -> Any:
        async with conn.cursor() as cursor:
            await cursor.execute(SELECT1)
            result = cursor.pgresult  # not `description`: it builds a Column list per call
            rows = await cursor.fetchall() if result is not None and result.nfields else None
            return cursor.rowcount, rows

    async def tx(conn: Any) -> Any:
        await conn.set_autocommit(False)
        async with conn.cursor() as cursor:
            await cursor.execute(SELECT1)
            rows = await cursor.fetchall()
        await conn.commit()
        await conn.set_autocommit(True)
        return rows

    async def stream(conn: Any) -> Any:
        cursor = AsyncClientServerCursor(
            conn, name=f"bench_{next(stream_ids)}", scrollable=False, withhold=True
        )
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


def _borrowing(pool: Any, body: Body, *, getconn: bool) -> Op:
    if getconn:

        async def via_getconn() -> Any:
            conn = await pool.getconn()
            try:
                return await body(conn)
            finally:
                await pool.putconn(conn)

        return via_getconn

    async def via_connection() -> Any:
        async with pool.connection() as conn:
            return await body(conn)

    return via_connection


def make_ops(driver: Any, pool: Any, config: Any) -> dict[str, dict[str, Op]]:
    """The operations of every group, keyed by impl."""
    from asyncopation.drivers.postgresql.driver import _Borrow

    def driver_query(sql: str) -> Op:
        async def op() -> Any:
            async with driver.connection() as conn:
                return await conn.fetch_all(sql, None)

        return op

    async def driver_execute() -> Any:
        async with driver.connection() as conn:
            return await conn.execute(SELECT1, None)

    async def driver_tx() -> Any:
        async with driver.connection() as conn:
            await conn.begin()
            rows = await conn.fetch_all(SELECT1, None)
            await conn.commit()
            return rows

    async def driver_stream() -> Any:
        async with driver.connection() as conn, conn.stream(STREAM, None, STREAM_CHUNK) as chunks:
            return sum([len(rows) async for rows in chunks])

    def noio(fake: _FakePool) -> dict[str, Op]:
        async def via_driver() -> Any:
            async with _Borrow(fake, config) as conn:  # ty: ignore[invalid-argument-type]
                return await conn.fetch_all(SELECT1, None)

        async def via_getconn() -> Any:
            raw = await fake.getconn()
            try:
                async with raw.cursor() as cursor:
                    await cursor.execute(SELECT1)
                    return await cursor.fetchall()
            finally:
                await fake.putconn(raw)

        return {"getconn": via_getconn, "driver": via_driver}

    bodies = _raw_bodies()
    ops: dict[str, dict[str, Op]] = {
        group: {
            "raw": _borrowing(pool, body, getconn=False),
            "getconn": _borrowing(pool, body, getconn=True),
        }
        for group, body in bodies.items()
    }
    ops["select1"]["driver"] = driver_query(SELECT1)
    ops["wide"]["driver"] = driver_query(WIDE)
    ops["execute"]["driver"] = driver_execute
    ops["tx"]["driver"] = driver_tx
    ops["stream"]["driver"] = driver_stream
    ops["noio"] = noio(_FakePool())
    ops["noio-yield"] = noio(_FakePool(yielding=True))
    return ops


# --------------------------------------------------------------------------- measurement
async def throughput(op: Op, tasks: int, seconds: float) -> dict[str, float] | None:
    """Run ``tasks`` workers for ~``seconds``; real elapsed time, latencies and client CPU/op."""
    started = time.perf_counter()
    stop = started + seconds
    latencies: list[int] = []

    async def worker() -> None:
        while time.perf_counter() < stop:
            begin = time.perf_counter_ns()
            await op()
            latencies.append(time.perf_counter_ns() - begin)

    cpu_started = time.process_time()
    async with asyncio.TaskGroup() as group:  # one failing worker cancels the others
        for _ in range(tasks):
            group.create_task(worker())
    cpu = time.process_time() - cpu_started
    elapsed = time.perf_counter() - started
    latencies.sort()
    count = len(latencies)
    if not count:
        return None  # nothing completed in the window: the round carries no information
    return {
        "ops_per_s": count / elapsed,
        "p50_us": latencies[count // 2] / 1000,
        "p95_us": latencies[int(count * 0.95)] / 1000,
        "p99_us": latencies[int(count * 0.99)] / 1000,
        "cpu_us": cpu / count * 1e6,
    }


def summarize_error(exc: BaseException) -> str:
    """``repr`` of the first leaf exception (a TaskGroup wraps worker errors in an ExceptionGroup)."""
    leaves = _leaves(exc)
    more = f" (+{len(leaves) - 1} more)" if len(leaves) > 1 else ""
    return repr(leaves[0]) + more


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)] or [exc]
    return [exc]


def known_suffixes() -> set[str]:
    """Every ``driver.*`` case name without its ``driver.`` prefix (valid ``--only`` values)."""
    return {n.split(".", 1)[1] for n in case_names()}


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 2:
        return (values[0], values[0], values[0]) if values else (0.0, 0.0, 0.0)
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, q2, q3


def _paired(
    data: dict[int, dict[str, float]], baseline: dict[int, dict[str, float]]
) -> list[tuple[float, float]]:
    """``(ratio, difference us)`` per round index present on both sides."""
    return [
        (data[i]["cpu_us"] / baseline[i]["cpu_us"], data[i]["cpu_us"] - baseline[i]["cpu_us"])
        for i in data.keys() & baseline.keys()
        if baseline[i]["cpu_us"]
    ]


async def _measure_group(
    group: str,
    tasks: int,
    impls: list[str],
    ops: dict[str, Op],
    seconds: float,
    rounds: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    failed: set[str] = set()
    for impl in impls:
        try:
            await throughput(ops[impl], tasks, 0.3)  # warm-up
        except Exception as exc:
            failed.add(impl)
            results[f"driver.{group}.{impl}.t{tasks}"] = {
                "status": "error",
                "error": summarize_error(exc),
            }
    live = [i for i in impls if i not in failed]
    rounds_data: dict[str, dict[int, dict[str, float]]] = {i: {} for i in live}
    for index in range(rounds):
        shift = index % len(live) if live else 0
        for impl in live[shift:] + live[:shift]:  # rotate: no impl always runs first
            if impl in failed:
                continue
            try:
                sample = await throughput(ops[impl], tasks, seconds)
            except Exception as exc:
                failed.add(impl)
                results[f"driver.{group}.{impl}.t{tasks}"] = {
                    "status": "error",
                    "error": summarize_error(exc),
                }
                continue
            if sample is not None:
                sample["load_1m"] = os.getloadavg()[0]
                rounds_data[impl][index] = sample
    for impl, data in rounds_data.items():
        if impl in failed:
            continue
        if not data:
            results[f"driver.{group}.{impl}.t{tasks}"] = {"status": "no-samples", "tasks": tasks}
            continue
        row: dict[str, Any] = {"status": "ok", "tasks": tasks}
        for key in ("ops_per_s", "p50_us", "p95_us", "p99_us", "cpu_us"):
            row[key] = statistics.median(r[key] for r in data.values())
        for baseline in ("raw", "getconn"):
            if impl == "driver" and baseline in rounds_data and baseline not in failed:
                pairs = _paired(data, rounds_data[baseline])
                if pairs:
                    q1, q2, q3 = _quartiles([ratio for ratio, _ in pairs])
                    row[f"ratio_{baseline}"] = {"median": q2, "q1": q1, "q3": q3}
                    if baseline == "getconn" and group in SINGLE_TASK_GROUPS:
                        d1, d2, d3 = _quartiles([diff for _, diff in pairs])
                        row["diff_getconn_us"] = {"median": d2, "q1": d1, "q3": d3}
        loads = [r["load_1m"] for r in data.values()]
        row["load_avg_1m"] = max(loads)
        row["load_avg_1m_min"] = min(loads)
        results[f"driver.{group}.{impl}.t{tasks}"] = row
    return results


async def run(
    driver: Any, config: Any, names: list[str], seconds: float, rounds: int
) -> dict[str, Any]:
    """Run the selected ``driver.*`` cases; ``config`` is the driver's ``PostgresConfig``."""
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        "", kwargs=config.conn_kwargs, open=False, name="bench-raw", **config.pool_kwargs
    )
    results: dict[str, Any] = {}
    try:
        await driver.open()
        await pool.open(wait=True)
        ops = make_ops(driver, pool, config)
        for tasks in TASKS:
            for group, impls in GROUPS.items():
                wanted = [i for i in impls if f"driver.{group}.{i}.t{tasks}" in names]
                if wanted and tasks in group_tasks(group):
                    results |= await _measure_group(
                        group, tasks, wanted, ops[group], seconds, rounds
                    )
    finally:
        await pool.close()
        await driver.close()
    return results
