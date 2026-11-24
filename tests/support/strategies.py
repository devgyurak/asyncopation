"""Hypothesis strategies that generate QuerySets over the catalog models.

Every generated QuerySet ends its ordering with `pk`, so ties never make the row order depend
on the query plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import QuerySet
from hypothesis import strategies as st

from harness.catalog.models import Book

_FILTERS: dict[str, st.SearchStrategy[Any]] = {
    "published": st.booleans(),
    "pages__gte": st.integers(0, 1300),
    "pages__lt": st.integers(0, 1300),
    "price__lte": st.decimals(min_value=Decimal("1.00"), max_value=Decimal("1000.00"), places=2),
    "title__startswith": st.sampled_from(["book-1", "book-2", "book-9", "nope"]),
    "author__name": st.sampled_from(["author-0", "author-3", "author-7", "missing"]),
    "author__publisher__country": st.sampled_from(["KR", "US", "FR", "JP"]),
    "published_at__isnull": st.booleans(),
    "tags__name": st.sampled_from(["tag-0", "tag-5", "tag-9"]),
    "author__profile__isnull": st.booleans(),
}
_ORDERINGS = ["pk", "-pk", "title", "-pages", "price", "author__name", "-published_at"]
_SELECT_RELATED = ["author", "author__publisher", "author__profile"]


@dataclass(frozen=True)
class QuerySetSpec:
    filters: tuple[tuple[str, Any], ...]
    excludes: tuple[tuple[str, Any], ...]
    order_by: tuple[str, ...]
    select_related: tuple[str, ...]
    window: tuple[int, int] | None


def _lookups(max_size: int) -> st.SearchStrategy[tuple[tuple[str, Any], ...]]:
    keys = st.lists(st.sampled_from(sorted(_FILTERS)), max_size=max_size, unique=True)
    return keys.flatmap(lambda ks: st.tuples(*(st.tuples(st.just(k), _FILTERS[k]) for k in ks)))


@st.composite
def _window(draw: st.DrawFn) -> tuple[int, int]:
    start = draw(st.integers(0, 30))
    return start, start + draw(st.integers(0, 30))


def queryset_specs() -> st.SearchStrategy[QuerySetSpec]:
    return st.builds(
        QuerySetSpec,
        filters=_lookups(3),
        excludes=_lookups(1),
        order_by=st.lists(st.sampled_from(_ORDERINGS), max_size=2, unique=True).map(tuple),
        select_related=st.lists(st.sampled_from(_SELECT_RELATED), max_size=2, unique=True).map(
            tuple
        ),
        window=st.none() | _window(),
    )


def build_queryset(spec: QuerySetSpec) -> QuerySet[Book]:
    qs = Book.objects.all()
    for lookup, value in spec.filters:
        qs = qs.filter(**{lookup: value})
    for lookup, value in spec.excludes:
        qs = qs.exclude(**{lookup: value})
    if spec.select_related:
        qs = qs.select_related(*spec.select_related)
    qs = qs.order_by(*spec.order_by, "pk")
    if spec.window:
        qs = qs[spec.window[0] : spec.window[1]]
    return qs
