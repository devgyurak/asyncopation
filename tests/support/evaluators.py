"""Pairs of (sync, async) ways to evaluate the same QuerySet.

`REFERENCE` uses Django core's async API (sync_to_async under the hood). It is not the subject
under test; it exists so the harness can verify itself before Asyncopation exists.
`ASYNCOPATION` uses the planned public API (`aq(qs)`) and is the subject under test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from tests.support.aio import collect


@dataclass(frozen=True)
class Evaluator:
    name: str
    sync: Callable[[QuerySet[Any]], Any]
    async_: Callable[[QuerySet[Any]], Awaitable[Any]]


def _aq(qs: QuerySet[Any]) -> Any:
    from asyncopation import aq  # ty: ignore[missing-module-member] - exists from Phase 1

    return aq(qs)


REFERENCE: dict[str, Evaluator] = {
    "list": Evaluator("list", list, lambda qs: collect(qs)),
    "count": Evaluator("count", lambda qs: qs.count(), lambda qs: qs.acount()),
    "first": Evaluator("first", lambda qs: qs.first(), lambda qs: qs.afirst()),
    "get": Evaluator("get", lambda qs: qs.get(), lambda qs: qs.aget()),
    "exists": Evaluator("exists", lambda qs: qs.exists(), lambda qs: qs.aexists()),
}

ASYNCOPATION: dict[str, Evaluator] = {
    "list": Evaluator("list", list, lambda qs: _aq(qs).alist()),
    "count": Evaluator("count", lambda qs: qs.count(), lambda qs: _aq(qs).acount()),
    "first": Evaluator("first", lambda qs: qs.first(), lambda qs: _aq(qs).afirst()),
    "get": Evaluator("get", lambda qs: qs.get(), lambda qs: _aq(qs).aget()),
    "exists": Evaluator("exists", lambda qs: qs.exists(), lambda qs: _aq(qs).aexists()),
}
