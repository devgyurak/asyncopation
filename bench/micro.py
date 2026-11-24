"""In-process micro-benchmarks: Django core async ORM vs Asyncopation, per call.

    uv run python -m bench.micro [--iterations 1000] [--only aget,gather3]

Asyncopation cases are reported as skipped until the API exists.

``driver.*`` cases (see ``bench/driver_micro.py``) compare the PostgreSQL driver with the raw
psycopg_pool calls it wraps at 1/8/64 tasks and report client CPU per operation and the paired
ratio vs the baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import platform
import time
from collections.abc import Awaitable, Callable
from typing import Any

from bench import driver_micro
from bench.db import BENCH_DB, ROOT, ensure_bench_db
from bench.stats import summarize

type Case = Callable[[], Awaitable[Any]]


def compile_cases(pg_version: int | None, settings_dict: dict[str, Any] | None) -> dict[str, Case]:
    """`compile_scope` enter/restore cost, and a scoped `as_sql()` against an unscoped one
    (`get_compiler(connection=wrapper)`, which skips the `connections[...]` lookup).

    PostgreSQL only (the MySQL composed wrapper arrives with #6); other backends report skipped.
    """
    from harness.catalog.models import Book

    wrapper: Any = None
    if pg_version is not None and settings_dict is not None:
        from asyncopation.compile.postgresql import wrapper_for
        from asyncopation.compile.server_info import PostgreSQLServerInfo

        wrapper = wrapper_for("default", settings_dict, PostgreSQLServerInfo(pg_version))

    def prepare() -> Any:
        if wrapper is None:
            raise NotImplementedError("compile-only wrapper exists for PostgreSQL only")
        return wrapper

    async def scope_enter_exit() -> Any:
        from asyncopation.compile.scope import compile_scope

        with compile_scope("default", prepare(), autocommit=True):
            return None

    async def scoped_as_sql() -> Any:
        from asyncopation.compile.scope import compile_scope

        with compile_scope("default", prepare(), autocommit=True):
            return Book.objects.filter(published=True).query.get_compiler(using="default").as_sql()

    async def unscoped_as_sql() -> Any:
        wrapper = prepare()
        return Book.objects.filter(published=True).query.get_compiler(connection=wrapper).as_sql()

    return {
        "asyncopation.compile_scope": scope_enter_exit,
        "asyncopation.as_sql_scoped": scoped_as_sql,
        "asyncopation.as_sql_unscoped": unscoped_as_sql,
    }


def build_cases(
    pk: int, pg_version: int | None = None, settings_dict: dict[str, Any] | None = None
) -> dict[str, Case]:
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
        **compile_cases(pg_version, settings_dict),
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


async def main_async(
    pk: int,
    pg_version: int | None,
    settings_dict: dict[str, Any] | None,
    iterations: int,
    warmup: int,
    only: set[str],
    driver: tuple[Any, Any] | None,
    driver_seconds: float,
    driver_rounds: int,
) -> dict[str, Any]:
    cases = build_cases(pk, pg_version, settings_dict)
    selected = {k: v for k, v in cases.items() if not only or k.split(".", 1)[1] in only}
    results = {name: await measure(case, iterations, warmup) for name, case in selected.items()}
    if driver is not None:
        names = driver_micro.selected(only)
        results |= await driver_micro.run(
            driver[0], driver[1], names, driver_seconds, driver_rounds
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument(
        "--only",
        default="",
        help=(
            "comma separated case suffixes: aget,gather3,list20,compile_scope,as_sql_scoped,"
            "as_sql_unscoped or driver cases such as select1.driver.t8 (the baselines of a "
            "selected driver case are added automatically)"
        ),
    )
    parser.add_argument(
        "--driver-seconds", type=float, default=1.0, help="seconds per driver.* round"
    )
    parser.add_argument("--driver-rounds", type=int, default=7, help="interleaved driver.* rounds")
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
    from django.db import connection

    pg_version = connection.pg_version if connection.vendor == "postgresql" else None  # sync probe
    only = {s for s in args.only.split(",") if s}
    known = {"aget", "gather3", "list20", "compile_scope", "as_sql_scoped", "as_sql_unscoped"}
    unknown = sorted(only - known - driver_micro.known_suffixes())
    if unknown:
        parser.error(f"--only matches no case: {', '.join(unknown)}")
    driver = build_driver(only)  # before the loop starts: resolving time zones reads from disk
    results = asyncio.run(
        main_async(
            pk,
            pg_version,
            connection.settings_dict,
            args.iterations,
            args.warmup,
            only,
            driver,
            args.driver_seconds,
            args.driver_rounds,
        )
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


def build_driver(only: set[str]) -> tuple[Any, Any] | None:
    """``(driver, config)`` when a ``driver.*`` case is selected on PostgreSQL, else ``None``."""
    if os.environ.get("ASYNCOPATION_DB", "postgresql") != "postgresql":
        return None
    if not driver_micro.selected(only):
        return None
    from django.conf import settings

    from asyncopation.drivers.postgresql import PostgresDriver
    from asyncopation.drivers.postgresql.settings import build_config

    config = build_config(
        "default",
        settings.DATABASES["default"],
        use_tz=settings.USE_TZ,
        time_zone=settings.TIME_ZONE,
    )
    return PostgresDriver(config), config


def render(payload: dict[str, Any]) -> str:
    lines = [
        "| case | status | p50 µs | p95 µs | p99 µs | ops/s | CPU µs/op | paired ratio (IQR) "
        "| driver-getconn µs | load | note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["results"]:
        if row["status"] != "ok":
            note = row.get("error") or row.get("reason", "")
            lines.append(
                f"| {row['case']} | {row['status']} | - | - | - | - | - | - | - | - | {note} |"
            )
            continue
        cpu = f"{row['cpu_us']:.1f}" if "cpu_us" in row else "-"
        ratios = [
            f"{row[key]['median']:.2f}x [{row[key]['q1']:.2f}-{row[key]['q3']:.2f}] vs {key[6:]}"
            for key in ("ratio_raw", "ratio_getconn")
            if key in row
        ]
        d = row.get("diff_getconn_us")
        diff = f"{d['median']:+.2f} [{d['q1']:+.2f}..{d['q3']:+.2f}]" if d else "-"
        load = (
            f"{row['load_avg_1m_min']:.0f}-{row['load_avg_1m']:.0f}"
            if "load_avg_1m" in row
            else "-"
        )
        lines.append(
            f"| {row['case']} | ok | {row['p50_us']:.0f} | {row['p95_us']:.0f} | {row['p99_us']:.0f} "
            f"| {row['ops_per_s']:.0f} | {cpu} | {'; '.join(ratios) or '-'} | {diff} | {load} | |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
