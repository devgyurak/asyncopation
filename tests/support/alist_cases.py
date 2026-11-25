"""Cases and outcomes for the `aq(...).alist()` integration tests (control flow lives here)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from harness.catalog.models import Author, Book
from tests.support.aio import wait_until
from tests.support.differential import (
    DifferentialResult,
    Outcome,
    run_differential,
    run_sync,
)
from tests.support.evaluators import ASYNCOPATION, Evaluator
from tests.support.seeded import books_manager_of
from tests.support.snapshot import snapshot_result

ALIAS = "default"
SLOW_SQL = "(SELECT pg_sleep(30)) IS NOT NULL"


def failing_statement() -> QuerySet[Book]:
    return Book.objects.extra(where=["bogus_col = 1"])


def unknown_alias() -> QuerySet[Book]:
    return Book.objects.using("nope")


def slow_statement() -> QuerySet[Book]:
    return Book.objects.extra(where=[SLOW_SQL])


def _books_ascending() -> QuerySet[Book]:
    return Book.objects.order_by("pk")[:5]


def _books_descending() -> QuerySet[Book]:
    return Book.objects.order_by("-pk")[:5]


def _manager_evaluator() -> Evaluator:
    from asyncopation import aq

    return Evaluator("manager", lambda manager: list(manager.all()), lambda m: aq(m).alist())


async def manager_differential(author: Author) -> DifferentialResult:
    """Sync `list(author.books.all())` against `await aq(author.books).alist()`."""

    def build() -> Any:
        return books_manager_of(author)

    return await run_differential(build, _manager_evaluator())


@dataclass(frozen=True)
class KnownRelatedIdentity:
    rows: int
    shared_with_owner: list[bool]


async def related_manager_identity(author: Author) -> KnownRelatedIdentity:
    """Whether every book of `await aq(author.books).alist()` caches `author` itself."""
    from asyncopation import aq

    books = await aq(books_manager_of(author)).alist()
    return KnownRelatedIdentity(
        len(books), [book._state.fields_cache.get("author") is author for book in books]
    )


async def wait_pool_settled(alias: str = ALIAS) -> None:
    """Wait until no connection of the alias is in use or waited for (replacements finished)."""
    from asyncopation.connection import registry

    async def settled() -> bool:
        stats = registry.pool_stats(alias)
        return stats.in_use == 0 and stats.waiting == 0

    await wait_until(settled)


def pool_in_use(alias: str = ALIAS) -> int:
    from asyncopation.connection import registry

    return registry.pool_stats(alias).in_use


@dataclass(frozen=True)
class GatherOutcome:
    sync: tuple[Outcome, Outcome]
    asynchronous: tuple[Outcome, Outcome]
    in_use_after: int


async def gathered_alists() -> GatherOutcome:
    """Two different `alist()` calls at once on one alias, with the sync results as reference."""
    from asyncopation import aq

    builds = (_books_ascending, _books_descending)
    sync = (
        await run_sync(builds[0], ASYNCOPATION["list"]),
        await run_sync(builds[1], ASYNCOPATION["list"]),
    )
    first, second = await asyncio.gather(
        aq(builds[0]()).alist(), aq(builds[1]()).alist(), return_exceptions=True
    )
    await wait_pool_settled()
    return GatherOutcome(
        sync,
        (_outcome(first), _outcome(second)),
        pool_in_use(),
    )


def _outcome(value: Any) -> Outcome:
    if isinstance(value, BaseException):
        return Outcome(None, type(value).__name__)
    return Outcome(snapshot_result(value), None)


@dataclass(frozen=True)
class CancelOutcome:
    cancelled: bool
    next_rows: int
    in_use_after: int


async def cancelled_alist_then_next() -> CancelOutcome:
    """Cancel an `alist()` while its statement runs, then run another `alist()` on the alias."""
    from asyncopation import aq
    from asyncopation.connection import registry

    task = asyncio.ensure_future(aq(slow_statement()).alist())

    async def statement_running() -> bool:
        return registry.in_flight(ALIAS) > 0

    await wait_until(statement_running)
    task.cancel()
    (outcome,) = await asyncio.gather(task, return_exceptions=True)
    await wait_pool_settled()
    rows = await aq(_books_ascending()).alist()
    await wait_pool_settled()
    return CancelOutcome(isinstance(outcome, asyncio.CancelledError), len(rows), pool_in_use())


@dataclass(frozen=True)
class SharedWrapperOutcome:
    results: tuple[list[Any], list[Any]]
    cached: list[Any]


async def concurrent_alists_on_one_wrapper() -> SharedWrapperOutcome:
    """Two concurrent `alist()` calls on the SAME wrapper, and the cache they leave behind."""
    from asyncopation import aq

    wrapper = aq(_books_ascending())
    first, second = await asyncio.gather(wrapper.alist(), wrapper.alist())
    return SharedWrapperOutcome((first, second), wrapper.queryset._result_cache)


@dataclass(frozen=True)
class CacheOutcome:
    original_cache: Any
    wrapper_cache: Any
    rows: list[Any]


async def alist_cache_placement() -> CacheOutcome:
    """Where `aq(qs).alist()` leaves its result cache: on the wrapper's clone, not on `qs`."""
    from asyncopation import aq

    original = Book.objects.order_by("pk")[:3]
    wrapper = aq(original)
    rows = await wrapper.alist()
    return CacheOutcome(original._result_cache, wrapper.queryset._result_cache, rows)
