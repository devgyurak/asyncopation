"""QuerySets that share a filled result cache in a user fetch mode (unit tests of the engine clones)."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.db.models import FETCH_PEERS  # ty: ignore[missing-module-member] - missing from django-types

from asyncopation._django import query as dq
from harness.catalog.models import Book


def filled_in_a_user_fetch_mode(queryset: Any) -> Any:
    """`queryset` with a filled cache and a fetch mode that is not FETCH_RAISE (what a prefetched
    manager or a synchronously evaluated QuerySet hands the wrapper)."""
    dq.set_cached_result(queryset, [])
    dq.set_fetch_mode(queryset, FETCH_PEERS)
    return queryset


class _SelfReturningAll(QuerySet):
    """A QuerySet subclass whose `all()` returns itself (legal: Django only promises a QuerySet)."""

    def all(self) -> Any:
        return self


def shared_sliced_combinator_with_self_returning_all() -> Any:
    """A sliced union, in a user fetch mode with a filled cache, of a QuerySet subclass whose
    `all()` returns itself: what `aget` must clone without touching."""
    union = Book.objects.union(Book.objects.all())
    shared = _SelfReturningAll(model=Book, query=union.query)[:50]
    return filled_in_a_user_fetch_mode(shared)
