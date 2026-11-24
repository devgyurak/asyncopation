"""In-process micro-benchmarks: Django core async ORM vs Asyncopation, per call.

    uv run python -m bench.micro [--iterations 1000] [--only aget,gather3]

Asyncopation cases are reported as skipped until the API exists.
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


async def main_async(pk: int, iterations: int, warmup: int, only: set[str]) -> dict[str, Any]:
    cases = build_cases(pk)
    selected = {k: v for k, v in cases.items() if not only or k.split(".", 1)[1] in only}
    return {name: await measure(case, iterations, warmup) for name, case in selected.items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument(
        "--only", default="", help="comma separated case suffixes (aget,gather3,list20)"
    )
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
    results = asyncio.run(main_async(pk, args.iterations, args.warmup, only))
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
    lines = ["| case | status | p50 µs | p95 µs | p99 µs | ops/s |", "|---|---|---|---|---|---|"]
    for row in payload["results"]:
        if row["status"] != "ok":
            lines.append(f"| {row['case']} | skipped | - | - | - | - |")
            continue
        lines.append(
            f"| {row['case']} | ok | {row['p50_us']:.0f} | {row['p95_us']:.0f} | {row['p99_us']:.0f} "
            f"| {row['ops_per_s']:.0f} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
