"""Cases and outcomes for the `aq(...).alist()` integration tests (control flow lives here)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet

from harness.catalog.models import Author, Book
from tests.support.aio import wait_until
from tests.support.alist_probe import recorded_statements
from tests.support.differential import (
    DifferentialResult,
    Outcome,
    run_differential,
    run_sync,
)
from tests.support.evaluators import ASYNCOPATION, Evaluator
from tests.support.seeded import books_manager_of, in_sync
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


def _chain_evaluator(chain: Callable[[Any], Any]) -> Evaluator:
    """Sync `list(chain(manager))` against `await chain(aq(manager)).alist()`."""
    from asyncopation import aq

    return Evaluator("chain", lambda m: list(chain(m)), lambda m: chain(aq(m)).alist())


async def manager_chain_differential(
    manager_of: Callable[[], Any], chain: Callable[[Any], Any]
) -> DifferentialResult:
    """A related manager followed by QuerySet methods (`chain`), sync against `aq(manager)`."""
    return await run_differential(manager_of, _chain_evaluator(chain))


def books_of_manager(tag: Any) -> Any:
    return tag.books


def books_all_of(tag: Any) -> Any:
    return tag.books.all()


def all_then_filter_by_peer(peer: str, source: Any) -> Any:
    return source.all().filter(tags__name=peer).order_by("pk")


def filter_by_peer(peer: str, source: Any) -> Any:
    return source.filter(tags__name=peer).order_by("pk")


async def tag_as_loaded_by_sync(tag: Any) -> Any:
    return tag


async def tag_as_loaded_by_aq(tag: Any) -> Any:
    """The same tag read through `aq()`: its instance carries FETCH_RAISE, and so does its manager."""
    from asyncopation import aq
    from harness.catalog.models import Tag

    (loaded,) = await aq(Tag.objects.filter(pk=tag.pk)).alist()
    return loaded


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

    @property
    def result_is_the_cache_list(self) -> tuple[bool, bool]:
        return (self.results[0] is self.cached, self.results[1] is self.cached)


async def concurrent_alists_on_one_wrapper() -> SharedWrapperOutcome:
    """Two concurrent `alist()` calls on the SAME wrapper, and the cache they leave behind."""
    from asyncopation import aq

    wrapper = aq(_books_ascending())
    first, second = await asyncio.gather(wrapper.alist(), wrapper.alist())
    return SharedWrapperOutcome((first, second), wrapper.queryset._result_cache)


def _union_values() -> QuerySet[Book]:
    union = Book.objects.filter(pk__lt=5).union(Book.objects.filter(pk__gt=40))
    return union.values("title").order_by("pages", "pk")


@dataclass(frozen=True)
class GatheredUnionOutcome:
    sync: Outcome
    asynchronous: tuple[Outcome, Outcome]
    annotations_before: list[str]
    annotations_after: list[str]


async def gathered_union_values_on_one_wrapper() -> GatheredUnionOutcome:
    """Two overlapping `alist()` calls on ONE union+values wrapper (compiling it adds
    `__orderbycol<N>` annotations to a Query they share), and whether the Query was touched."""
    from asyncopation import aq

    sync = await run_sync(_union_values, ASYNCOPATION["list"])
    wrapper = aq(_union_values())
    before = list(wrapper.queryset.query.annotations)
    first, second = await asyncio.gather(wrapper.alist(), wrapper.alist(), return_exceptions=True)
    return GatheredUnionOutcome(
        sync, (_outcome(first), _outcome(second)), before, list(wrapper.queryset.query.annotations)
    )


@dataclass(frozen=True)
class CacheOutcome:
    original_cache: Any
    wrapper_cache: Any
    rows: list[Any]


async def alist_cache_placement() -> CacheOutcome:
    """Where `aq(qs).alist()` leaves its result cache: on the wrapper's clone, not on `qs`."""

    return await _cache_outcome(Book.objects.order_by("pk")[:3])


async def alist_cache_placement_of_fetch_raise() -> CacheOutcome:
    """The same for a QuerySet the user already switched to FETCH_RAISE: still a clone gets filled."""
    from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member] - missing from django-types

    return await _cache_outcome(Book.objects.order_by("pk")[:3].fetch_mode(FETCH_RAISE))


async def _cache_outcome(original: QuerySet[Book]) -> CacheOutcome:
    from asyncopation import aq

    wrapper = aq(original)
    rows = await wrapper.alist()
    return CacheOutcome(original._result_cache, wrapper.queryset._result_cache, rows)


@dataclass(frozen=True)
class PrefetchOutcome:
    statements: list[str]
    async_pks: list[Any]
    sync_pks: list[Any]


def _author_with_prefetched_books() -> Any:
    queryset = Author.objects.prefetch_related("books").filter(books__isnull=False)
    return queryset.order_by("pk").first()


async def alist_of_a_prefetched_manager() -> PrefetchOutcome:
    """`aq(author.books)` after `prefetch_related("books")`: the cached rows, no statement."""
    from asyncopation import aq

    author = await in_sync(_author_with_prefetched_books)
    with recorded_statements() as log:
        rows = await aq(author.books).alist()
    expected = author.books.all()
    return PrefetchOutcome(list(log), [r.pk for r in rows], [r.pk for r in expected])
