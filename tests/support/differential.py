"""Differential runner: evaluate the same QuerySet with the sync ORM and an async evaluator.

`build` must be a zero-argument callable returning a *fresh* QuerySet each call, so result
caches can never leak between the two sides.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.db import connections
from django.db.models import QuerySet

from tests.support.evaluators import Evaluator
from tests.support.snapshot import Snapshot, snapshot_result

type QuerySetFactory = Callable[[], QuerySet[Any]]


@dataclass(frozen=True)
class Outcome:
    snapshot: Snapshot | None
    error: str | None

    @property
    def rows(self) -> tuple[Any, ...]:
        """Rows of a list result (empty for errors and single results)."""
        return self.snapshot[1] if self.snapshot and self.snapshot[0] == "list" else ()


@dataclass(frozen=True)
class DifferentialResult:
    sync_outcome: Outcome
    async_outcome: Outcome


def error_name(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


async def run_sync(build: QuerySetFactory, evaluator: Evaluator) -> Outcome:
    def evaluate() -> Outcome:
        try:
            return Outcome(snapshot_result(evaluator.sync(build())), None)
        except Exception as exc:
            return Outcome(None, error_name(exc))
        finally:
            # Close the worker thread's connection; otherwise dropping the test DB hangs.
            connections.close_all()

    return await sync_to_async(evaluate)()


async def run_async(build: QuerySetFactory, evaluator: Evaluator) -> Outcome:
    try:
        return Outcome(snapshot_result(await evaluator.async_(build())), None)
    except Exception as exc:
        return Outcome(None, error_name(exc))


async def run_differential(build: QuerySetFactory, evaluator: Evaluator) -> DifferentialResult:
    sync_outcome = await run_sync(build, evaluator)
    async_outcome = await run_async(build, evaluator)
    return DifferentialResult(sync_outcome, async_outcome)
