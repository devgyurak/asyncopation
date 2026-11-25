"""Helpers for the `aq()` wrapper unit tests (no database, no control flow in test modules)."""

from __future__ import annotations

import inspect
from typing import Any

from django.db.models import Manager, QuerySet

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


def prefilled_manager() -> SharedQuerysetManager:
    """A real Manager whose QuerySet already has its result cache (a related manager after
    `prefetch_related`)."""
    manager = SharedQuerysetManager()
    manager.shared._result_cache = []
    return manager


def raise_mode_manager() -> SharedQuerysetManager:
    """A real Manager whose `get_queryset()` returns ONE shared QuerySet that is already FETCH_RAISE
    (what a custom manager returning a class-level QuerySet of an `aq()` user looks like)."""
    from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member] - missing from django-types

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


SHARED_OTHER_MODE = Book.objects.all()


class ForeignModeQuerySet(QuerySet):
    """A QuerySet with a user method that returns a shared QuerySet in another fetch mode."""

    def shared_other_mode(self) -> QuerySet[Book]:
        return SHARED_OTHER_MODE


def foreign_mode_queryset() -> QuerySet[Book]:
    return ForeignModeQuerySet(model=Book)


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


def raise_mode_books() -> QuerySet[Book]:
    """A Book QuerySet that already carries FETCH_RAISE."""
    from django.db.models import FETCH_RAISE  # ty: ignore[missing-module-member] - missing from django-types

    return Book.objects.all().fetch_mode(FETCH_RAISE)


async def alist_after_mutating_the_result(wrapper: Any) -> list[Any]:
    """`alist()`, mutate the returned list, `alist()` again: the second result."""
    first = await wrapper.alist()
    first.append("mutated")
    return await wrapper.alist()
