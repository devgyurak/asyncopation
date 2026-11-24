"""Hypothesis strategies that generate QuerySets over the catalog models.

Every generated QuerySet ends its ordering with `pk`, so ties never make the row order depend
on the query plan. Annotations are picked by name from `ANNOTATIONS` (fresh expressions per
build) and may appear in the ordering.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db.models import Avg, Case, Count, Expression, F, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Coalesce
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

# Aggregates use relations the seed populates: every author has books (never NULL), books have
# 0-3 tags (NULL and non-NULL mixed, so NULL ordering is exercised). Combining aggregates over
# two multi-valued relations (tags and author__books) multiplies rows and inflates the values;
# that is intended - both sides of a differential run the same SQL.
ANNOTATIONS: dict[str, Callable[[], Expression | F]] = {
    "n_tags": lambda: Count("tags", distinct=True),
    "tag_id_sum": lambda: Sum("tags__id"),
    "tag_id_sum_or_zero": lambda: Coalesce(Sum("tags__id"), Value(0)),
    "author_pages": lambda: Sum("author__books__pages"),
    "author_avg_price": lambda: Avg("author__books__price"),
    "double_pages": lambda: F("pages") * 2,
    "length": lambda: Case(
        When(Q(pages__gte=500), then=Value("long")),
        When(Q(pages__gte=200), then=Value("medium")),
        default=Value("short"),
    ),
}


@dataclass(frozen=True)
class QuerySetSpec:
    filters: tuple[tuple[str, Any], ...]
    excludes: tuple[tuple[str, Any], ...]
    order_by: tuple[str, ...]
    select_related: tuple[str, ...]
    window: tuple[int, int] | None
    annotations: tuple[str, ...] = ()  # names from ANNOTATIONS, sorted


def _lookups(max_size: int) -> st.SearchStrategy[tuple[tuple[str, Any], ...]]:
    keys = st.lists(st.sampled_from(sorted(_FILTERS)), max_size=max_size, unique=True)
    return keys.flatmap(lambda ks: st.tuples(*(st.tuples(st.just(k), _FILTERS[k]) for k in ks)))


@st.composite
def _window(draw: st.DrawFn) -> tuple[int, int]:
    start = draw(st.integers(0, 30))
    return start, start + draw(st.integers(0, 30))


def _orderings(annotations: tuple[str, ...]) -> st.SearchStrategy[tuple[str, ...]]:
    choices = _ORDERINGS + [f"{sign}{name}" for name in annotations for sign in ("", "-")]
    return st.lists(st.sampled_from(choices), max_size=2, unique=True).map(tuple)


@st.composite
def queryset_specs(draw: st.DrawFn) -> QuerySetSpec:
    annotations = tuple(
        sorted(draw(st.lists(st.sampled_from(sorted(ANNOTATIONS)), max_size=2, unique=True)))
    )
    return QuerySetSpec(
        filters=draw(_lookups(3)),
        excludes=draw(_lookups(1)),
        order_by=draw(_orderings(annotations)),
        select_related=draw(
            st.lists(st.sampled_from(_SELECT_RELATED), max_size=2, unique=True).map(tuple)
        ),
        window=draw(st.none() | _window()),
        annotations=annotations,
    )


def annotated_spec(annotations: tuple[str, ...], order_by: tuple[str, ...]) -> QuerySetSpec:
    """A spec over all books with only annotations and ordering (example-based tests)."""
    return QuerySetSpec(
        filters=(),
        excludes=(),
        order_by=order_by,
        select_related=(),
        window=None,
        annotations=tuple(sorted(annotations)),
    )


def build_queryset(spec: QuerySetSpec) -> QuerySet[Book]:
    qs = Book.objects.all()
    for lookup, value in spec.filters:
        qs = qs.filter(**{lookup: value})
    for lookup, value in spec.excludes:
        qs = qs.exclude(**{lookup: value})
    if spec.select_related:
        qs = qs.select_related(*spec.select_related)
    if spec.annotations:
        qs = qs.annotate(**{name: ANNOTATIONS[name]() for name in spec.annotations})
    qs = qs.order_by(*spec.order_by, "pk")
    if spec.window:
        qs = qs[spec.window[0] : spec.window[1]]
    return qs
