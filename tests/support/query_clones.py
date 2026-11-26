"""QuerySets that share a filled result cache in a user fetch mode (unit tests of the engine clones)."""

from __future__ import annotations

from typing import Any

from django.db.models import (
    FETCH_PEERS,  # ty: ignore[missing-module-member] - missing from django-types
    QuerySet,
)

from asyncopation._django import query as dq
from harness.catalog.models import Book, Order


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


class _SelfReturningFilter(QuerySet):
    """A QuerySet subclass whose `filter()` returns itself."""

    def filter(self, *args: Any, **kwargs: Any) -> Any:
        return self


class _SelfReturningOrderBy(QuerySet):
    """A QuerySet subclass whose `order_by()` returns itself."""

    def order_by(self, *field_names: Any) -> Any:
        return self


def shared_sliced_with_self_returning_filter() -> Any:
    """A sliced QuerySet whose `filter()` returns itself. Sliced so `get_clone` skips `order_by()`:
    on an unsliced one `order_by()` always builds a fresh clone, which would hide a missing guard."""
    return filled_in_a_user_fetch_mode(
        _SelfReturningFilter(model=Book, query=Book.objects.all().query)[:50]
    )


def shared_unordered_with_self_returning_order_by() -> Any:
    """An unordered `Order` QuerySet whose `order_by()` returns itself."""
    return filled_in_a_user_fetch_mode(
        _SelfReturningOrderBy(model=Order, query=Order.objects.all().query)
    )
