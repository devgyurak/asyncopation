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
