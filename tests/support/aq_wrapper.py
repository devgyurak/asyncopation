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


def has_attribute(wrapper: Any, name: str) -> bool:
    return hasattr(wrapper, name)


def filtered_pair() -> tuple[QuerySet[Book], QuerySet[Book]]:
    return Book.objects.filter(published=True), Book.objects.filter(pages__gt=100)


def sql_of(value: Any) -> str:
    """SQL of a QuerySet or of a wrapper around one."""
    queryset = getattr(value, "queryset", value)
    return str(queryset.query)
