"""Executor micro-benchmarks for the ADR-0005 hooks: the cost of ``_run`` with 0 and 1 observers.

Cases are named ``hooks.<executor>_<call>.<subscribers>``:

* executors: ``borrowed`` (``BorrowedExecutor``: ``ensure_driver``, the in-flight counter, the
  borrow and the call), ``pinned`` (a ``PinnedExecutor`` on one connection: owner check and the
  call) and ``connection`` (the ``RecordingConnection`` method called directly, no executor: the
  baseline the executor cases are compared with)
* calls: ``fetch`` (``fetch_all``) and ``stream`` (open, read one chunk of one row, close)
* subscribers: ``s0`` (nobody observes: the zero-overhead path, ADR-0005 Consequences) and ``s1``
  (one scoped no-op observer, registered around the whole measurement)

The driver is a ``RecordingDriver`` (no network, no server), installed as the open driver of the
alias ``hooks-bench`` in the running loop, so the numbers resolve the executor's and the hooks' own
cost per call. Each round times a batch of calls per case (interleaved, the order rotated every
round); ``p50`` is the median over the rounds of the mean time per call, and ``p95`` / ``p99`` are
percentiles of those per-round means, not of single calls (the ``note`` column says so). The note
also gives ``+x.xx us vs connection``: the case's median minus the ``connection`` case of the same
call, i.e. the executor's (and, for ``s1``, the observer's) own cost; the ``RecordingConnection``
call is in both and cancels out, but the ``borrowed`` cases also pay the fake borrow
(``driver.connection()`` and a new ``RecordingConnection``), so their overhead is an upper bound.

Before/after a hook change: keep the current ``bench/`` and run it against the base commit's
``src/`` (a detached worktree of the base, with ``PYTHONPATH=<base>/src`` and the current
``bench/`` copied in), alternating runs, and compare the ``s0`` overheads (within noise is the
target, ADR-0005 Validation). ``s1`` cases are skipped where ``asyncopation.observe`` does not
exist.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
from collections.abc import Awaitable, Callable, Generator
from typing import Any

from bench.stats import percentile

type Op = Callable[[], Awaitable[Any]]

ALIAS = "hooks-bench"
ROWS = [(1,)]
CASES = (
    "connection_fetch.s0",
    "connection_stream.s0",
    "borrowed_fetch.s0",
    "borrowed_fetch.s1",
    "pinned_fetch.s0",
    "pinned_fetch.s1",
    "borrowed_stream.s0",
    "borrowed_stream.s1",
    "pinned_stream.s0",
    "pinned_stream.s1",
)


def known_suffixes() -> set[str]:
    return set(CASES)


def selected(only: set[str]) -> list[str]:
    """The ``hooks.*`` case suffixes ``--only`` selects (all of them when ``--only`` is empty)."""
    return [name for name in CASES if not only or name in only]


def _noop(event: Any) -> None:
    return None


@contextlib.contextmanager
def _subscribers(count: int) -> Generator[None]:
    if count == 0:
        yield
        return
    import asyncopation

    with asyncopation.observe(_noop):  # AttributeError before the hooks exist: skipped
        yield


async def _install() -> tuple[Any, Any, Any]:
    """A ``RecordingDriver`` as the open driver of ``ALIAS``, plus a pinned executor on one of its
    connections owned by the current task."""
    from asyncopation.connection import registry
    from asyncopation.connection.executor import BorrowedExecutor, PinnedExecutor
    from asyncopation.testing import RecordingConnection, RecordingDriver

    driver = RecordingDriver()
    await driver.open()
    registry.entry_or_create(ALIAS, asyncio.get_running_loop()).driver = driver
    task = asyncio.current_task()
    assert task is not None
    pinned = PinnedExecutor(ALIAS, driver, RecordingConnection(driver), task)
    return driver, BorrowedExecutor(ALIAS), pinned


def _ops(driver: Any, borrowed: Any, pinned: Any) -> dict[str, Op]:
    from asyncopation.testing import RecordingConnection

    connection = RecordingConnection(driver)

    def fetch(executor: Any) -> Op:
        async def op() -> Any:
            driver._queue.append(ROWS)
            return await executor.fetch_all("SELECT 1", ())

        return op

    def stream(executor: Any) -> Op:
        async def op() -> Any:
            driver._queue.append(ROWS)
            async with executor.stream("SELECT 1", (), 50) as chunks:
                async for chunk in chunks:
                    return chunk
            return None

        return op

    return {
        "connection_fetch": fetch(connection),
        "connection_stream": stream(connection),
        "borrowed_fetch": fetch(borrowed),
        "pinned_fetch": fetch(pinned),
        "borrowed_stream": stream(borrowed),
        "pinned_stream": stream(pinned),
    }


async def _batch(op: Op, subscribers: int, calls: int) -> float:
    """Mean microseconds per call over one batch."""
    with _subscribers(subscribers):
        start = time.perf_counter_ns()
        for _ in range(calls):
            await op()
        elapsed = time.perf_counter_ns() - start
    return elapsed / calls / 1000


async def run(names: list[str], calls: int, rounds: int) -> dict[str, dict[str, Any]]:
    from asyncopation.connection import registry

    driver, borrowed, pinned = await _install()
    ops = _ops(driver, borrowed, pinned)
    results: dict[str, dict[str, Any]] = {}
    plan: list[tuple[str, Op, int]] = []
    for name in names:
        call, subscribers = name.split(".")
        op, count = ops[call], int(subscribers[1:])
        try:
            await _batch(op, count, 50)  # warm-up; tells whether the case can run at all
        except (AttributeError, ImportError) as exc:
            results[f"hooks.{name}"] = {
                "status": "skipped",
                "reason": f"{type(exc).__name__}: {exc}",
            }
            continue
        plan.append((name, op, count))
    samples: dict[str, list[float]] = {name: [] for name, _, _ in plan}
    try:
        for index in range(rounds):
            shift = index % max(1, len(plan))
            for name, op, count in plan[shift:] + plan[:shift]:
                samples[name].append(await _batch(op, count, calls))
                driver._statements.clear()  # the log would otherwise grow for the whole run
    finally:
        entry = registry.entry(ALIAS, asyncio.get_running_loop())
        if entry is not None:
            registry.forget(entry)
        await driver.close()
    for name, values in samples.items():
        median = statistics.median(values)
        quartiles = statistics.quantiles(values, n=4) if len(values) > 1 else [median] * 3
        results[f"hooks.{name}"] = {
            "status": "ok",
            "iterations": calls * rounds,
            "mean_us": statistics.fmean(values),
            "p50_us": median,  # percentiles are over the per-round means, not single calls
            "p95_us": percentile(values, 95),
            "p99_us": percentile(values, 99),
            "ops_per_s": 1e6 / median,
            "iqr_us": [quartiles[0], quartiles[2]],
        }
    for name, row in results.items():
        if row["status"] != "ok":
            continue
        call = name.split(".")[1].split("_")[1]
        base = results.get(f"hooks.connection_{call}.s0")
        notes = ["p95/p99 of round means"]
        if base is not None and base["status"] == "ok" and base is not row:
            row["overhead_us"] = row["p50_us"] - base["p50_us"]
            notes.append(f"{row['overhead_us']:+.2f} us vs connection")
        row["note"] = "; ".join(notes)
    return results
