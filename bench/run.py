"""Load benchmark orchestrator: WSGI+sync vs ASGI+core async vs ASGI+Asyncopation.

    uv run python -m bench.run --configs wsgi,asgi-core,asyncopation \
        --scenarios single,gather,nplus1,write --duration 20s --vus 50 --workers 4

Per (config, scenario): start the server, wait for /health/, probe the endpoint (configs that
cannot serve it yet are reported as unavailable), run k6 (native binary if on PATH, otherwise
the docker compose `k6` service) while sampling DB connections and server RSS.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
import platform
import shutil
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psutil

from bench.db import BENCH_DB, ROOT, bench_env, ensure_bench_db

RESULTS = ROOT / "bench/results"
CONFIGS = {
    "wsgi": ("/sync", ["gunicorn", "harness.site.wsgi:application", "--workers", "{workers}",
                       "--worker-class", "gthread", "--threads", "4", "--bind", "{host}:{port}", "--log-level", "warning"]),
    "asgi-core": ("/core", ["uvicorn", "harness.site.asgi:application", "--workers", "{workers}",
                            "--host", "{host}", "--port", "{port}", "--no-access-log", "--lifespan", "off"]),
    "asyncopation": ("/aopt", ["uvicorn", "harness.site.asgi:application", "--workers", "{workers}",
                               "--host", "{host}", "--port", "{port}", "--no-access-log", "--lifespan", "on"]),
}  # fmt: skip
# (method, path) used to probe availability and to warm up each scenario.
PROBES = {
    "single": ("GET", "/book/{pk}/"),
    "gather": ("GET", "/dashboard/"),
    "nplus1": ("GET", "/books-with-authors/"),
    "write": ("POST", "/orders/"),
}


@dataclass
class Samples:
    connections: list[int] = field(default_factory=list)
    rss_mb: list[float] = field(default_factory=list)


class ConnectionCounter:
    """Counts server connections to the bench DB over one long-lived monitoring connection."""

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor
        self.conn: Any = None

    async def __aenter__(self) -> ConnectionCounter:
        if self.vendor == "postgresql":
            import psycopg

            self.conn = await psycopg.AsyncConnection.connect(
                host="127.0.0.1", port=25432, user="asyncopation", password="asyncopation",
                dbname="asyncopation", autocommit=True,
            )  # fmt: skip
        else:
            import asyncmy

            port = 23306 if self.vendor == "mysql" else 23307
            self.conn = await asyncmy.connect(
                host="127.0.0.1",
                port=port,
                user="asyncopation",
                password="asyncopation",
                autocommit=True,
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.vendor == "postgresql":
            await self.conn.close()
        else:
            self.conn.close()

    async def count(self) -> int:
        if self.vendor == "postgresql":
            cur = await self.conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                [BENCH_DB],
            )
            row = await cur.fetchone()
        else:
            async with self.conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM information_schema.processlist "
                    "WHERE db = %s AND id <> CONNECTION_ID()",
                    [BENCH_DB],
                )
                row = await cur.fetchone()
        return int(row[0]) if row else 0


def tree_rss_mb(pid: int) -> float:
    with contextlib.suppress(psutil.NoSuchProcess):
        proc = psutil.Process(pid)
        procs = [proc, *proc.children(recursive=True)]
        return sum(p.memory_info().rss for p in procs if p.is_running()) / 2**20
    return 0.0


async def sample(vendor: str, pid: int, samples: Samples, stop: asyncio.Event) -> None:
    async with ConnectionCounter(vendor) as counter:
        while not stop.is_set():
            samples.connections.append(await counter.count())
            samples.rss_mb.append(tree_rss_mb(pid))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.25)


async def wait_healthy(url: str, proc: asyncio.subprocess.Process, within: float = 30) -> None:
    async with httpx.AsyncClient() as client, asyncio.timeout(within):
        while True:
            if proc.returncode is not None:
                raise RuntimeError(f"server exited with {proc.returncode}")
            with contextlib.suppress(httpx.TransportError):
                if (await client.get(f"{url}/health/")).status_code == 200:
                    return
            await asyncio.sleep(0.2)


async def warm_up(
    method: str, url: str, payload: dict[str, int], requests: int = 200, concurrency: int = 10
) -> None:
    """Prime worker processes, DB connections and caches before measuring."""
    async with httpx.AsyncClient() as client:
        for _ in range(requests // concurrency):
            await asyncio.gather(
                *(client.request(method, url, json=payload) for _ in range(concurrency))
            )


def k6_command(env: dict[str, str], summary: Path) -> list[str]:
    script_args = ["run", "--quiet", "--summary-export"]
    flags = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
    if shutil.which("k6"):
        return ["k6", *script_args, str(summary), *flags, str(ROOT / "bench/k6/scenarios.js")]
    return [
        "docker", "compose", "--profile", "bench", "run", "--rm", "k6",
        *script_args, f"/results/{summary.name}", *flags, "/scripts/scenarios.js",
    ]  # fmt: skip


def parse_summary(path: Path) -> dict[str, float]:
    metrics = json.loads(path.read_text())["metrics"]
    duration = metrics["http_req_duration"]
    failed = metrics.get("http_req_failed", {})
    return {
        "p50_ms": duration["med"],
        "p95_ms": duration["p(95)"],
        "p99_ms": duration["p(99)"],
        "rps": metrics["http_reqs"]["rate"],
        "error_rate": failed.get("value", failed.get("rate", 0.0)),
    }


async def run_one(
    config: str, scenario: str, args: argparse.Namespace, pk_range: tuple[int, int]
) -> dict[str, Any]:
    prefix, template = CONFIGS[config]
    if config == "asyncopation" and args.vendor != "postgresql":
        # `--lifespan on` needs LifespanApp, which the harness wires on PostgreSQL only (no async
        # MySQL/MariaDB read path until #7b); starting it would abort uvicorn and the whole run.
        row = {"config": config, "scenario": scenario}
        return row | {
            "status": "unavailable",
            "reason": f"no async read path for {args.vendor} until #7b",
        }
    host = "127.0.0.1" if shutil.which("k6") else "0.0.0.0"  # docker k6 must reach the host
    cmd = [part.format(workers=args.workers, host=host, port=args.port) for part in template]
    base = f"http://127.0.0.1:{args.port}"
    log_path = RESULTS / f"server-{config}-{scenario}-{dt.datetime.now():%H%M%S}.log"
    log = log_path.open("w")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", *cmd, cwd=ROOT, env=bench_env(), stdout=log, stderr=log
    )
    row: dict[str, Any] = {"config": config, "scenario": scenario, "server_log": log_path.name}
    try:
        try:
            await wait_healthy(base, proc)
        except (RuntimeError, TimeoutError) as exc:  # keep the other rows of the run
            return row | {"status": "error", "reason": f"server did not start: {exc}"}
        method, path = PROBES[scenario]
        url = base + prefix + path.format(pk=pk_range[0])
        payload = {"book": pk_range[0], "quantity": 1}
        async with httpx.AsyncClient() as client:
            status = (await client.request(method, url, json=payload)).status_code
        if status >= 400:
            return row | {"status": "unavailable", "reason": f"probe returned HTTP {status}"}
        await warm_up(method, url, payload)
        summary = RESULTS / f"k6-{config}-{scenario}-{dt.datetime.now():%H%M%S}.json"
        target = base if shutil.which("k6") else f"http://host.docker.internal:{args.port}"
        env = {
            "BASE_URL": target, "PREFIX": prefix, "SCENARIO": scenario, "VUS": str(args.vus),
            "DURATION": args.duration, "BOOK_MIN": str(pk_range[0]), "BOOK_MAX": str(pk_range[1]),
        }  # fmt: skip
        samples, stop = Samples(), asyncio.Event()
        sampler = asyncio.create_task(sample(args.vendor, proc.pid, samples, stop))
        k6 = await asyncio.create_subprocess_exec(*k6_command(env, summary), cwd=ROOT)
        await k6.wait()
        stop.set()
        await sampler
        if k6.returncode not in (0, 99):  # 99 = thresholds crossed, results still valid
            return row | {"status": "error", "reason": f"k6 exited with {k6.returncode}"}
        return row | {
            "status": "ok",
            **parse_summary(summary),
            "db_conn_peak": max(samples.connections, default=0),
            "db_conn_avg": statistics.fmean(samples.connections) if samples.connections else 0,
            "rss_peak_mb": max(samples.rss_mb, default=0.0),
        }
    finally:
        proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=10)
        if proc.returncode is None:
            proc.kill()
        log.close()


def book_pk_range() -> tuple[int, int]:
    os.environ.update(
        {"ASYNCOPATION_DB_NAME": BENCH_DB, "DJANGO_SETTINGS_MODULE": "harness.settings"}
    )
    import django

    django.setup()
    from django.db.models import Max, Min

    from harness.catalog.models import Book

    agg = Book.objects.aggregate(lo=Min("pk"), hi=Max("pk"))
    return int(agg["lo"]), int(agg["hi"])


def render(rows: list[dict[str, Any]]) -> str:
    head = "| config | scenario | status | p50 ms | p95 ms | p99 ms | req/s | errors | DB conn peak | RSS MB |"
    lines = [head, "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["status"] != "ok":
            lines.append(
                f"| {r['config']} | {r['scenario']} | {r['status']}: {r.get('reason', '')} |||||||"
            )
            continue
        lines.append(
            f"| {r['config']} | {r['scenario']} | ok | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {r['p99_ms']:.1f} "
            f"| {r['rps']:.0f} | {r['error_rate']:.2%} | {r['db_conn_peak']} | {r['rss_peak_mb']:.0f} |"
        )
    return "\n".join(lines)


async def main_async(args: argparse.Namespace, pk_range: tuple[int, int]) -> list[dict[str, Any]]:
    rows = []
    for config in args.configs.split(","):
        for scenario in args.scenarios.split(","):
            print(f"-> {config} / {scenario}", file=sys.stderr)
            rows.append(await run_one(config, scenario, args, pk_range))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--configs", default="wsgi,asgi-core,asyncopation")
    parser.add_argument("--scenarios", default="single,gather,nplus1,write")
    parser.add_argument("--duration", default="20s")
    parser.add_argument("--vus", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--skip-seed", action="store_true")
    args = parser.parse_args()
    args.vendor = os.environ.get("ASYNCOPATION_DB", "postgresql")
    RESULTS.mkdir(exist_ok=True)
    if not args.skip_seed:
        ensure_bench_db()
    rows = asyncio.run(main_async(args, book_pk_range()))
    payload = {
        "kind": "load",
        "meta": {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "backend": args.vendor,
            "machine": f"{platform.system()} {platform.machine()} cpus={os.cpu_count()}",
            "duration": args.duration,
            "vus": args.vus,
            "workers": args.workers,
        },
        "results": rows,
    }
    out = RESULTS / f"load-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(payload, indent=2))
    print(render(rows))
    print(f"\nwritten: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
