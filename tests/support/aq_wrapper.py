"""Helpers for the `aq()` wrapper unit tests (no database, no control flow in test modules)."""

from __future__ import annotations

import inspect
from typing import Any

from django.db.models import QuerySet

from harness.catalog.models import Book

IMPLEMENTED_ASYNC_METHODS = frozenset({"alist"})


def unimplemented_async_names() -> list[str]:
    """Coroutine methods of Django's QuerySet that the wrapper does not implement yet."""
    names = [
        name
        for name, member in inspect.getmembers(QuerySet)
        if inspect.iscoroutinefunction(member) and name not in IMPLEMENTED_ASYNC_METHODS
    ]
    return sorted(names)


def async_coroutine_names_of_django() -> list[str]:
    """Every `a<name>` coroutine method Django's QuerySet defines (read from Django, not from us)."""
    names = [
        name
        for name, member in inspect.getmembers(QuerySet)
        if inspect.iscoroutinefunction(member) and name.startswith("a")
    ]
    return sorted(names)


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
