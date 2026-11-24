"""In-process micro-benchmarks: Django core async ORM vs Asyncopation, per call.

    uv run python -m bench.micro [--iterations 1000] [--only aget,gather3]

Asyncopation cases are reported as skipped until the API exists.

``driver.*`` cases compare the PostgreSQL driver (``driver.connection()`` + ``fetch_all``) with a
raw ``AsyncConnectionPool`` + ``AsyncClientCursor`` on the *same* pool at 1/8/64 concurrent tasks
and report client CPU per operation (``cpu_us``). They measure driver overhead only (``SELECT 1``).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bench.db import BENCH_DB, ROOT, ensure_bench_db
from bench.stats import summarize

type Case = Callable[[], Awaitable[Any]]


def build_cases(pk: int) -> dict[str, Case]:
    from harness.catalog.models import Author, Book, Tag

    async def core_aget() -> Any:
        return await Book.objects.aget(pk=pk)

    async def core_gather3() -> Any:
        return await asyncio.gather(
            Author.objects.afirst(), Book.objects.acount(), Tag.objects.afirst()
        )

    async def core_list20() -> Any:
        return [b async for b in Book.objects.all()[:20]]

    async def aopt_aget() -> Any:
        return await Book.aobjects.aget(pk=pk)  # ty: ignore[missing-attribute]

    async def aopt_gather3() -> Any:
        return await asyncio.gather(
            Author.aobjects.afirst(),  # ty: ignore[missing-attribute]
            Book.aobjects.acount(),  # ty: ignore[missing-attribute]
            Tag.aobjects.afirst(),  # ty: ignore[missing-attribute]
        )

    async def aopt_list20() -> Any:
        return await Book.aobjects.all()[:20].alist()  # ty: ignore[missing-attribute]

    return {
        "core.aget": core_aget,
        "core.gather3": core_gather3,
        "core.list20": core_list20,
        "asyncopation.aget": aopt_aget,
        "asyncopation.gather3": aopt_gather3,
        "asyncopation.list20": aopt_list20,
    }


async def measure(case: Case, iterations: int, warmup: int) -> dict[str, Any]:
    try:
        for _ in range(warmup):
            await case()
    except (AttributeError, ImportError, NotImplementedError) as exc:
        return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        await case()
        samples.append((time.perf_counter_ns() - start) / 1000)
    return {"status": "ok", "iterations": iterations, **summarize(samples)}


DRIVER_TASKS = (1, 8, 64)
DRIVER_SQL = "SELECT 1::int"


async def _throughput(op: Case, tasks: int, seconds: float) -> tuple[float, ...]:
    """Run ``tasks`` workers for ``seconds``: (ops/s, p50 us, p95 us, p99 us, client CPU us/op)."""
    stop = time.perf_counter() + seconds
    latencies: list[int] = []

    async def worker() -> None:
        while time.perf_counter() < stop:
            start = time.perf_counter_ns()
            await op()
            latencies.append(time.perf_counter_ns() - start)

    cpu_start = time.process_time()
    await asyncio.gather(*(worker() for _ in range(tasks)))
    cpu = time.process_time() - cpu_start
    latencies.sort()
    count = len(latencies)
    return (
        count / seconds,
        latencies[count // 2] / 1000,
        latencies[int(count * 0.95)] / 1000,
        latencies[int(count * 0.99)] / 1000,
        cpu / count * 1e6,
    )


async def driver_results(seconds: float, rounds: int, only: set[str]) -> dict[str, Any]:
    """Raw pool vs ``PostgresDriver`` on the same pool, interleaved rounds, median per metric."""
    from django.conf import settings

    if os.environ.get("ASYNCOPATION_DB", "postgresql") != "postgresql":
        return {}
    from asyncopation.drivers.postgresql import PostgresDriver

    driver = PostgresDriver.from_settings(
        "default",
        settings.DATABASES["default"],
        use_tz=settings.USE_TZ,
        time_zone=settings.TIME_ZONE,
    )
    await driver.open()
    pool = driver._pool  # the raw baseline must use the driver's own pool
    assert pool is not None

    async def raw() -> Any:
        async with pool.connection() as conn, conn.cursor() as cursor:
            await cursor.execute(DRIVER_SQL)
            return await cursor.fetchall()

    async def via_driver() -> Any:
        async with driver.connection() as conn:
            return await conn.fetch_all(DRIVER_SQL, None)

    results: dict[str, Any] = {}
    try:
        for tasks in DRIVER_TASKS:
            names = {f"driver.raw.t{tasks}": raw, f"driver.asyncopation.t{tasks}": via_driver}
            names = {n: c for n, c in names.items() if not only or n.split(".", 1)[1] in only}
            if not names:
                continue
            for case in names.values():
                await _throughput(case, tasks, 0.3)  # warm-up
            samples: dict[str, list[tuple[float, ...]]] = {n: [] for n in names}
            for index in range(rounds):
                ordered = list(names.items())
                for name, case in ordered if index % 2 == 0 else ordered[::-1]:
                    samples[name].append(await _throughput(case, tasks, seconds))
            for name, rows in samples.items():
                ops, p50, p95, p99, cpu = (statistics.median(r[i] for r in rows) for i in range(5))
                results[name] = {
                    "status": "ok",
                    "tasks": tasks,
                    "ops_per_s": ops,
                    "p50_us": p50,
                    "p95_us": p95,
                    "p99_us": p99,
                    "cpu_us": cpu,
                }
    finally:
        await driver.close()
    return results


async def main_async(
    pk: int, iterations: int, warmup: int, only: set[str], driver_seconds: float, driver_rounds: int
) -> dict[str, Any]:
    cases = build_cases(pk)
    selected = {k: v for k, v in cases.items() if not only or k.split(".", 1)[1] in only}
    results = {name: await measure(case, iterations, warmup) for name, case in selected.items()}
    return results | await driver_results(driver_seconds, driver_rounds, only)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument(
        "--only",
        default="",
        help="comma separated case suffixes (aget,gather3,list20,raw.t1,asyncopation.t64,...)",
    )
    parser.add_argument(
        "--driver-seconds", type=float, default=1.0, help="seconds per driver.* round"
    )
    parser.add_argument("--driver-rounds", type=int, default=5, help="interleaved driver.* rounds")
    parser.add_argument("--skip-seed", action="store_true")
    args = parser.parse_args()

    if not args.skip_seed:
        ensure_bench_db()
    os.environ["ASYNCOPATION_DB_NAME"] = BENCH_DB
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "harness.settings")
    import django

    django.setup()
    from harness.catalog.models import Book

    pk = Book.objects.order_by("pk").values_list("pk", flat=True)[0]  # sync, before the loop starts
    only = {s for s in args.only.split(",") if s}
    results = asyncio.run(
        main_async(pk, args.iterations, args.warmup, only, args.driver_seconds, args.driver_rounds)
    )
    payload = {
        "kind": "micro",
        "meta": {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "backend": os.environ.get("ASYNCOPATION_DB", "postgresql"),
            "python": platform.python_version(),
            "machine": f"{platform.system()} {platform.machine()} cpus={os.cpu_count()}",
        },
        "results": [{"case": name, **data} for name, data in results.items()],
    }
    out = ROOT / "bench/results" / f"micro-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(payload, indent=2))
    print(render(payload))
    print(f"\nwritten: {out.relative_to(ROOT)}")


def render(payload: dict[str, Any]) -> str:
    lines = [
        "| case | status | p50 µs | p95 µs | p99 µs | ops/s | CPU µs/op |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in payload["results"]:
        if row["status"] != "ok":
            lines.append(f"| {row['case']} | skipped | - | - | - | - | - |")
            continue
        cpu = f"{row['cpu_us']:.1f}" if "cpu_us" in row else "-"
        lines.append(
            f"| {row['case']} | ok | {row['p50_us']:.0f} | {row['p95_us']:.0f} | {row['p99_us']:.0f} "
            f"| {row['ops_per_s']:.0f} | {cpu} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
