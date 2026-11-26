"""Helpers for the `aq()` wrapper unit tests (no database, no control flow in test modules)."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from django.db.models import FETCH_RAISE, Manager, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from harness.catalog.models import Book

IMPLEMENTED_ASYNC_METHODS = frozenset({"alist"})

_EXECUTOR_STATEMENTS = ("fetch_all", "execute", "execute_many", "stream")


def forbid_executor_statements(monkeypatch: Any, tripwire: Any) -> None:
    """Replace every statement method of both executors with ``tripwire``."""
    for cls in ("BorrowedExecutor", "PinnedExecutor"):
        for method in _EXECUTOR_STATEMENTS:
            monkeypatch.setattr(f"asyncopation.connection.executor.{cls}.{method}", tripwire)


def _is_async_twin(name: str, member: Any) -> bool:
    """`a<name>` is callable and `<name>` is a callable of QuerySet too (not a predicate on the kind
    of function, so coroutines and async generators like `aiterator` are both found)."""
    return name.startswith("a") and callable(member) and callable(getattr(QuerySet, name[1:], None))


def async_coroutine_names_of_django() -> list[str]:
    """Every `a<name>` async twin Django's QuerySet defines (read from Django, not from us)."""
    names = [name for name, member in inspect.getmembers(QuerySet) if _is_async_twin(name, member)]
    return sorted(names)


def unimplemented_async_names() -> list[str]:
    """Async twins of Django's QuerySet that the wrapper does not implement yet."""
    return sorted(set(async_coroutine_names_of_django()) - IMPLEMENTED_ASYNC_METHODS)


def unhandled_django_coroutines() -> list[str]:
    """Django coroutine names that are neither implemented on the wrapper nor refused as pending."""
    from asyncopation.query import AsyncQuerySet
    from asyncopation.query.queryset import _DJANGO_COROUTINES

    implemented = {n for n in async_coroutine_names_of_django() if n in vars(AsyncQuerySet)}
    return sorted(set(async_coroutine_names_of_django()) - implemented - _DJANGO_COROUTINES)


def unlisted_sync_evaluators() -> list[str]:
    """Sync names whose async twin (`a<name>`) Django defines but `_SYNC_EVALUATORS` lacks."""
    from asyncopation.query.queryset import _SYNC_EVALUATORS

    twins = {name[1:] for name in async_coroutine_names_of_django()}
    sync_names = {name for name in twins if callable(getattr(QuerySet, name, None))}
    return sorted(sync_names - set(_SYNC_EVALUATORS))


def call_named(wrapper: Any, name: str) -> Any:
    """Access `wrapper.<name>` and call it with no arguments (either step may raise)."""
    return getattr(wrapper, name)()


def prefilled_queryset(rows: list[Any]) -> QuerySet[Book]:
    """A Book QuerySet whose result cache is already filled (evaluating it needs no I/O)."""
    queryset = Book.objects.all()
    queryset._result_cache = rows
    return queryset


def book_manager() -> Any:
    return Book.objects


def fresh_books() -> QuerySet[Book]:
    return Book.objects.all()


def sync_evaluator_names() -> list[str]:
    """Every name the wrapper refuses as a synchronous evaluator (the full `_SYNC_EVALUATORS`)."""
    from asyncopation.query.queryset import _SYNC_EVALUATORS

    return sorted(_SYNC_EVALUATORS)


def related_books_manager() -> Any:
    """`author.books` of an unsaved author with a primary key (building it needs no database)."""
    from harness.catalog.models import Author
    from tests.support.seeded import books_manager_of

    return books_manager_of(Author(pk=1, name="x"))


class SharedDuckSource:
    """Not a Manager or QuerySet: `get_queryset()` hands out ONE shared QuerySet every time."""

    def __init__(self, *, sticky: bool = False) -> None:
        self.shared = Book.objects.all()
        self.shared.query.filter_is_sticky = sticky

    def get_queryset(self) -> QuerySet[Book]:
        return self.shared


class SharedQuerysetManager(Manager):
    """A real Manager (a descriptor-free instance) whose `get_queryset()` is this one QuerySet."""

    def __init__(self) -> None:
        super().__init__()
        self.shared = Book.objects.all()

    def get_queryset(self) -> QuerySet[Book]:
        return self.shared


CACHED_ROW = object()


def prefilled_manager() -> SharedQuerysetManager:
    """A real Manager whose QuerySet already has its result cache, holding the one `CACHED_ROW` (a
    related manager after `prefetch_related`)."""
    manager = SharedQuerysetManager()
    manager.shared._result_cache = [CACHED_ROW]
    return manager


def raise_mode_manager() -> SharedQuerysetManager:
    """A real Manager whose `get_queryset()` returns ONE shared QuerySet that is already FETCH_RAISE
    (what a custom manager returning a class-level QuerySet of an `aq()` user looks like)."""
    manager = SharedQuerysetManager()
    manager.shared = manager.shared.fetch_mode(FETCH_RAISE)
    return manager


class NotAQuerySetManager(Manager):
    """A real Manager whose `get_queryset()` returns something that is no QuerySet."""

    def get_queryset(self) -> Any:
        return 42


def prefilled_duck_source() -> Any:
    """A duck-typed source (no Manager) whose `get_queryset()` returns a filled-cache QuerySet."""
    source = SharedDuckSource()
    source.shared._result_cache = []
    return source


def raise_mode_books() -> QuerySet[Book]:
    """A Book QuerySet that already carries FETCH_RAISE."""
    return Book.objects.all().fetch_mode(FETCH_RAISE)


def foreign_mode_queryset() -> QuerySet[Book]:
    """A QuerySet whose user methods return QuerySets it stores (one in another fetch mode, one in
    FETCH_RAISE, two with a filled cache): a user method is not a Django chaining method, so its
    result is not a fresh clone. The stored QuerySets belong to this call's class, so a clone of
    the QuerySet still reaches them and no test shares them with another."""
    other_mode = Book.objects.all()
    raise_mode = raise_mode_books()
    cached = raise_mode_books()
    cached._result_cache = [CACHED_ROW]
    cached_other_mode = Book.objects.all()
    cached_other_mode._result_cache = [CACHED_ROW]

    class ForeignModeQuerySet(QuerySet):
        def shared_other_mode(self) -> QuerySet[Book]:
            return other_mode

        def shared_raise_mode(self) -> QuerySet[Book]:
            return raise_mode

        def shared_cached(self) -> QuerySet[Book]:
            return cached

        def shared_cached_other_mode(self) -> QuerySet[Book]:
            return cached_other_mode

    return ForeignModeQuerySet(model=Book)


def stored_raise_mode_wrapper() -> Any:
    """A wrapper whose user method returns a stored FETCH_RAISE QuerySet."""
    return aq_of(foreign_mode_queryset())


class StickyForeignQuerySet(QuerySet):
    """A QuerySet whose user method returns a QuerySet in another fetch mode with a pending sticky
    filter (`tag.books.all()` of an unsaved tag)."""

    def sticky_other_mode(self) -> QuerySet[Any]:
        return sticky_tag_books()


def sticky_foreign_queryset() -> QuerySet[Book]:
    return StickyForeignQuerySet(model=Book)


class IdentityQuerySet(QuerySet):
    """User chaining methods that break the 'return a new QuerySet' contract."""

    def same(self) -> QuerySet[Book]:
        return self

    def echo(self, other: QuerySet[Book]) -> QuerySet[Book]:
        return other

    def echo_second(self, first: QuerySet[Book], second: QuerySet[Book]) -> QuerySet[Book]:
        return second

    def echo_keyword(self, *, first: QuerySet[Book], other: QuerySet[Book]) -> QuerySet[Book]:
        return other


def identity_queryset() -> QuerySet[Book]:
    return IdentityQuerySet(model=Book).fetch_mode(FETCH_RAISE)


def combination_operands() -> tuple[Any, Any, Any, Any]:
    """Two user-held FETCH_RAISE QuerySets (one empty) and the wrappers `aq()` makes of them."""
    from asyncopation import aq

    empty = Book.objects.none().order_by().fetch_mode(FETCH_RAISE)
    other = Book.objects.filter(published=True).order_by().fetch_mode(FETCH_RAISE)
    return empty, other, aq(empty), aq(other)


def shared_with(result: Any, *operands: Any) -> list[bool]:
    """For each operand: is it the very QuerySet the wrapper `result` holds?"""
    return [result.queryset is operand for operand in operands]


def _case(name: str, build: Any) -> Any:
    return pytest.param(build, id=name)


# (wrapper of the empty one, wrapper of the other, the empty one, the other) -> combined wrapper.
# Django returns an operand itself whenever one side is empty.
COMBINATIONS = [
    _case("or-empty-wrapped-left", lambda we, wo, e, o: we | wo),
    _case("or-empty-wrapped-right", lambda we, wo, e, o: wo | we),
    _case("or-empty-left-plain-right", lambda we, wo, e, o: we | o),
    _case("or-empty-right-plain-right", lambda we, wo, e, o: wo | e),
    _case("and-empty-wrapped-left", lambda we, wo, e, o: we & wo),
    _case("and-empty-wrapped-right", lambda we, wo, e, o: wo & we),
    _case("and-empty-plain-right", lambda we, wo, e, o: wo & e),
    _case("xor-empty-left-plain-right", lambda we, wo, e, o: we ^ o),
    _case("xor-empty-wrapped-left", lambda we, wo, e, o: we ^ wo),
    _case("xor-empty-wrapped-right", lambda we, wo, e, o: wo ^ we),
    _case("xor-empty-plain-right", lambda we, wo, e, o: wo ^ e),
    _case("union-empty-receiver", lambda we, wo, e, o: we.union(o)),
    _case("union-empty-receiver-second-argument", lambda we, wo, e, o: we.union(e, o)),
    _case("union-empty-receiver-wrapped-second-argument", lambda we, wo, e, o: we.union(we, wo)),
    _case("union-empty-receiver-wrapped-argument", lambda we, wo, e, o: we.union(wo)),
    _case("union-empty-argument-wrapped", lambda we, wo, e, o: wo.union(we)),
    _case("union-empty-argument", lambda we, wo, e, o: wo.union(e)),
    _case("intersection-empty-receiver", lambda we, wo, e, o: we.intersection(o)),
    _case("intersection-empty-argument", lambda we, wo, e, o: wo.intersection(e)),
    _case("difference-empty-receiver", lambda we, wo, e, o: we.difference(o)),
    _case("difference-empty-argument", lambda we, wo, e, o: wo.difference(e)),
]


async def user_caches_after_alist_of(build: Any) -> list[Any]:
    """`alist()` of a combination: the result caches of the two QuerySets the caller holds and of
    the two QuerySets the operand wrappers hold."""
    empty, other, wrapped_empty, wrapped_other = combination_operands()
    await build(wrapped_empty, wrapped_other, empty, other).alist()
    return [
        empty._result_cache,
        other._result_cache,
        wrapped_empty.queryset._result_cache,
        wrapped_other.queryset._result_cache,
    ]


def sticky_tag_books() -> QuerySet[Any]:
    """`tag.books.all()` of an unsaved tag: a QuerySet with a pending sticky filter."""
    from harness.catalog.models import Tag

    return read_attribute(Tag(pk=1, name="x"), "books").all()


class DuckSource:
    """Not a Manager or QuerySet: only `get_queryset()` is available."""

    def get_queryset(self) -> QuerySet[Book]:
        return fresh_books()


def read_attribute(obj: Any, name: str) -> Any:
    return getattr(obj, name)


def has_attribute(wrapper: Any, name: str) -> bool:
    return hasattr(wrapper, name)


def filtered_pair() -> tuple[QuerySet[Book], QuerySet[Book]]:
    return Book.objects.filter(published=True), Book.objects.filter(pages__gt=100)


def sql_of(value: Any) -> str:
    """SQL of a QuerySet or of a wrapper around one."""
    queryset = getattr(value, "queryset", value)
    return str(queryset.query)


def uninitialised_wrapper() -> Any:
    """An AsyncQuerySet whose `_queryset` slot was never filled (what `copy`/`pickle` can create)."""
    from asyncopation.query import AsyncQuerySet

    return object.__new__(AsyncQuerySet)


def cached_wrapper() -> Any:
    """A wrapper over a QuerySet whose result cache is already filled."""
    return aq_of(prefilled_queryset([]))


def aq_of(queryset: Any) -> Any:
    from asyncopation import aq

    return aq(queryset)


def tag_books_manager() -> Any:
    """`tag.books` (many-to-many reverse manager) of an unsaved tag with a primary key."""
    from harness.catalog.models import Tag

    return read_attribute(Tag(pk=1, name="x"), "books")


def sliced_with_step(wrapper: Any, step: int) -> Any:
    return wrapper[::step]


def class_attribute_of(wrapper: Any) -> Any:
    """`wrapper.model` is a class (the Django model), not a callable to wrap."""
    return wrapper.model


async def all_then_alist(wrapper: Any) -> list[Any]:
    """`all()` then `alist()` on a wrapper: the rows."""
    return await wrapper.all().alist()


async def alist_after_mutating_the_result(wrapper: Any) -> list[Any]:
    """`alist()`, mutate the returned list, `alist()` again: the second result."""
    first = await wrapper.alist()
    first.append("mutated")
    return await wrapper.alist()


def fill_cache(wrapper: Any) -> Any:
    """Fill the wrapped QuerySet's cache the way `alist()` does, without a statement."""
    wrapper.queryset._result_cache = []
    return wrapper


class NotAQuerySetDuckSource:
    """Not a Manager or QuerySet: `get_queryset()` returns something that is no QuerySet."""

    def get_queryset(self) -> Any:
        return 42


def all_then_filter(wrapper: Any) -> Any:
    return wrapper.all().filter(published=True)


def foreign_mode_identity_wrapper() -> Any:
    """A wrapper built by hand over a QuerySet that is NOT in FETCH_RAISE (`aq()` never makes one);
    its user method returns the receiver."""
    from asyncopation.query import AsyncQuerySet

    return AsyncQuerySet(IdentityQuerySet(model=Book))


def prefetched_operands() -> tuple[Any, Any, Any, Any]:
    """A manager with a filled cache, its wrapper, an empty wrapper and an empty plain QuerySet."""
    from asyncopation import aq

    manager = prefilled_manager()
    empty = Book.objects.none().fetch_mode(FETCH_RAISE)
    return manager, aq(manager), aq(empty), empty


# (wrapper of the prefetched manager, wrapper of an empty QuerySet, that empty QuerySet) -> result.
# Django returns the cached QuerySet itself in each (`cached | none() is cached`).
PREFETCHED_COMBINATIONS = [
    _case("or-prefetched-left", lambda wp, we, e: wp | we),
    _case("or-prefetched-right", lambda wp, we, e: we | wp),
    _case("or-prefetched-left-plain-right", lambda wp, we, e: wp | e),
    _case("xor-prefetched-left", lambda wp, we, e: wp ^ we),
    _case("xor-prefetched-right", lambda wp, we, e: we ^ wp),
    _case("xor-prefetched-left-plain-right", lambda wp, we, e: wp ^ e),
]


def filled_by_alist_wrapper() -> Any:
    """A wrapper over a plain QuerySet whose cache `alist()` filled (no statement)."""
    return fill_cache(aq_of(fresh_books()))


def prefetched_identity_manager() -> SharedQuerysetManager:
    """A real Manager whose QuerySet has a filled cache and a user method returning its receiver."""
    manager = SharedQuerysetManager()
    manager.shared = IdentityQuerySet(model=Book)
    manager.shared._result_cache = [CACHED_ROW]
    return manager


def overriding_queryset(method: str, *, cached: bool = False) -> tuple[Any, QuerySet[Book]]:
    """A FETCH_RAISE QuerySet of a subclass that overrides `method` (`all`, `__or__`, `__and__`,
    `__xor__`, `__getitem__` or `filter`) to return a stored FETCH_RAISE QuerySet (unevaluated, or
    with a filled cache when `cached`), and that stored QuerySet."""
    stored = raise_mode_books()
    if cached:
        stored._result_cache = [CACHED_ROW]

    def override(self: Any, *args: Any, **kwargs: Any) -> QuerySet[Book]:
        return stored

    override.__name__ = method  # a method is known by its name, as a def would give it
    subclass = type("OverridingQuerySet", (QuerySet,), {method: override})
    return subclass(model=Book).fetch_mode(FETCH_RAISE), stored


def counting_queryset() -> tuple[Any, list[int]]:
    """A FETCH_RAISE QuerySet that Django chains as usual, and the list that records each
    `fetch_mode()` call made on it or on its clones (an extra clone into FETCH_RAISE shows up)."""
    calls: list[int] = []

    class CountingQuerySet(QuerySet):
        def fetch_mode(self, mode: Any) -> Any:
            calls.append(1)
            return super().fetch_mode(mode)

    return CountingQuerySet(model=Book).fetch_mode(FETCH_RAISE), calls


# (overridden method, how a wrapper of that QuerySet reaches it)
OVERRIDE_CASES = [
    pytest.param("all", lambda wrapper: wrapper.all(), id="all"),
    pytest.param("__or__", lambda wrapper: wrapper | wrapper, id="or"),
    pytest.param("__and__", lambda wrapper: wrapper & wrapper, id="and"),
    pytest.param("__xor__", lambda wrapper: wrapper ^ wrapper, id="xor"),
    pytest.param("__getitem__", lambda wrapper: wrapper[:1], id="slice"),
    pytest.param("filter", lambda wrapper: wrapper.filter(pk=1), id="filter"),
]
