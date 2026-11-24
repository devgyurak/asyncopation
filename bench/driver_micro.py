"""Driver micro-benchmarks: ``PostgresDriver`` vs the raw psycopg_pool calls it wraps.

Cases are named ``driver.<group>.<impl>.t<tasks>``:

* groups: ``select1`` (``SELECT 1``), ``wide`` (one 15-column row), ``execute`` (``execute()`` with
  rowcount), ``tx`` (begin / statement / commit), ``stream`` (200 rows in chunks of 50)
* impls: ``raw`` (``pool.connection()`` + cursor), ``getconn`` (``getconn``/``putconn``, the
  cheapest possible baseline, ``select1`` only) and ``driver``
* tasks: 1, 8 and 64 concurrent workers

Each round runs every impl of a group for ``seconds`` (interleaved, order alternating per round).
The stable metric is client CPU per operation; the report adds the per-round *paired* ratio
``driver / raw`` (median and inter-quartile range over the rounds) and the load average, because
the absolute numbers move with the machine. The raw baselines use their own pool built from the
driver's own configuration (same sizes and connection kwargs). They do not run the driver's
per-connection session setup (isolation level, TimeZone, role), which costs nothing per
operation. The ``execute`` and ``stream`` baselines use the same idioms as the driver (no
``Column`` list per call; the client-binding named cursor).
"""

from __future__ import annotations

import asyncio
import itertools
import os
import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Any

type Op = Callable[[], Awaitable[Any]]

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
    "wide": ("raw", "driver"),
    "execute": ("raw", "driver"),
    "tx": ("raw", "driver"),
    "stream": ("raw", "driver"),
}


def case_names() -> list[str]:
    return [f"driver.{g}.{i}.t{t}" for t in TASKS for g, impls in GROUPS.items() for i in impls]


def selected(only: set[str]) -> list[str]:
    """Selected cases plus their baselines (``raw``, and ``getconn`` for select1) so the paired
    ratio can always be computed."""
    chosen = {n for n in case_names() if not only or n.split(".", 1)[1] in only}
    wanted = set(chosen)
    for name in chosen:
        _, group, _, tasks = name.split(".")
        wanted.add(f"driver.{group}.raw.{tasks}")
        if group == "select1":
            wanted.add(f"driver.select1.getconn.{tasks}")
    return [n for n in case_names() if n in wanted]


def make_ops(driver: Any, pool: Any) -> dict[str, dict[str, Op]]:
    """The operations of every group, keyed by impl. ``pool`` is the raw baseline's own pool."""
    from asyncopation.drivers.postgresql.connection import AsyncClientServerCursor

    stream_ids = itertools.count(1)

    def raw_query(sql: str) -> Op:
        async def op() -> Any:
            async with pool.connection() as conn, conn.cursor() as cursor:
                await cursor.execute(sql)
                return await cursor.fetchall()

        return op

    def driver_query(sql: str) -> Op:
        async def op() -> Any:
            async with driver.connection() as conn:
                return await conn.fetch_all(sql, None)

        return op

    async def getconn_select1() -> Any:
        conn = await pool.getconn()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT1)
                return await cursor.fetchall()
        finally:
            await pool.putconn(conn)

    async def raw_execute() -> Any:
        async with pool.connection() as conn, conn.cursor() as cursor:
            await cursor.execute(SELECT1)
            result = cursor.pgresult  # not `description`: it builds a Column list per call
            rows = await cursor.fetchall() if result is not None and result.nfields else None
            return cursor.rowcount, rows

    async def driver_execute() -> Any:
        async with driver.connection() as conn:
            return await conn.execute(SELECT1, None)

    async def raw_tx() -> Any:
        async with pool.connection() as conn:
            await conn.set_autocommit(False)
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT1)
                rows = await cursor.fetchall()
            await conn.commit()
            await conn.set_autocommit(True)
            return rows

    async def driver_tx() -> Any:
        async with driver.connection() as conn:
            await conn.begin()
            rows = await conn.fetch_all(SELECT1, None)
            await conn.commit()
            return rows

    async def raw_stream() -> Any:
        async with pool.connection() as conn:
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

    async def driver_stream() -> Any:
        async with driver.connection() as conn, conn.stream(STREAM, None, STREAM_CHUNK) as chunks:
            return sum([len(rows) async for rows in chunks])

    return {
        "select1": {
            "raw": raw_query(SELECT1),
            "getconn": getconn_select1,
            "driver": driver_query(SELECT1),
        },
        "wide": {"raw": raw_query(WIDE), "driver": driver_query(WIDE)},
        "execute": {"raw": raw_execute, "driver": driver_execute},
        "tx": {"raw": raw_tx, "driver": driver_tx},
        "stream": {"raw": raw_stream, "driver": driver_stream},
    }


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
    await asyncio.gather(*(worker() for _ in range(tasks)))
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


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 2:
        return (values[0], values[0], values[0]) if values else (0.0, 0.0, 0.0)
    q1, q2, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return q1, q2, q3


async def run(
    driver: Any, config: Any, names: list[str], seconds: float, rounds: int
) -> dict[str, Any]:
    """Run the selected ``driver.*`` cases; ``config`` is the driver's ``PostgresConfig``."""
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        "", kwargs=config.conn_kwargs, open=False, name="bench-raw", **config.pool_kwargs
    )
    await driver.open()
    await pool.open(wait=True)
    results: dict[str, Any] = {}
    try:
        ops = make_ops(driver, pool)
        for tasks in TASKS:
            for group, impls in GROUPS.items():
                wanted = [i for i in impls if f"driver.{group}.{i}.t{tasks}" in names]
                if not wanted:
                    continue
                for impl in wanted:
                    await throughput(ops[group][impl], tasks, 0.3)  # warm-up
                rounds_data: dict[str, list[dict[str, float]]] = {i: [] for i in wanted}
                for index in range(rounds):
                    for impl in wanted if index % 2 == 0 else wanted[::-1]:
                        sample = await throughput(ops[group][impl], tasks, seconds)
                        if sample is not None:
                            sample["load_1m"] = os.getloadavg()[0]
                            rounds_data[impl].append(sample)
                for impl, data in rounds_data.items():
                    if not data:
                        continue
                    row: dict[str, Any] = {"status": "ok", "tasks": tasks}
                    for key in ("ops_per_s", "p50_us", "p95_us", "p99_us", "cpu_us"):
                        row[key] = statistics.median(r[key] for r in data)
                    for baseline in ("raw", "getconn"):
                        if impl != baseline and baseline in rounds_data:
                            ratios = [
                                d["cpu_us"] / b["cpu_us"]
                                for d, b in zip(data, rounds_data[baseline], strict=False)
                                if b["cpu_us"]
                            ]
                            if ratios:
                                q1, q2, q3 = _quartiles(ratios)
                                row[f"ratio_{baseline}"] = {"median": q2, "q1": q1, "q3": q3}
                    loads = [d["load_1m"] for d in data]
                    row["load_avg_1m"] = max(loads)
                    row["load_avg_1m_min"] = min(loads)
                    results[f"driver.{group}.{impl}.t{tasks}"] = row
    finally:
        await pool.close()
        await driver.close()
    return results
