"""Hypothesis strategies that generate QuerySets over the catalog models.

Every generated QuerySet ends its ordering with `pk`, so ties never make the row order depend
on the query plan. Annotations are picked by name from `ANNOTATIONS` (fresh expressions per
build) and may appear in the ordering.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal

from django.core.exceptions import FieldError
from django.db.models import Avg, Case, Count, Expression, F, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Coalesce
from hypothesis import strategies as st

from harness.catalog.models import Book
from tests.support.differential import error_name

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
    # Reverse FK join: duplicate rows are identical, so a window stays deterministic. The seed
    # leaves `released` NULL for every fifth book, so both values select rows.
    "editions__released__isnull": st.booleans(),
}
_ORDERINGS = ["pk", "-pk", "title", "-pages", "price", "author__name", "-published_at"]
_SELECT_RELATED = ["author", "author__publisher", "author__profile"]

# only()/defer() candidates: Book's concrete fields and, per select_related path, fields of the
# related model (a field path implies the traversed relations are loaded).
_BOOK_FIELDS = ["title", "price", "pages", "published", "published_at", "metadata"]
_RELATED_FIELDS = {
    "author": ["author__name", "author__email"],
    "author__publisher": ["author__publisher__country"],
    "author__profile": ["author__profile__bio"],
}
_INVALID_ONE_IN = 20  # one deferral in this many may break a select_related traversal
type Deferred = tuple[Literal["only", "defer"], tuple[str, ...]]
# values() / values_list() / values_list(flat=True) / values_list(named=True) over these fields (and
# the spec's annotations). A projection replaces only()/defer(): Django ignores them for rows that
# are not model instances, so combining them would test nothing.
type Projection = tuple[Literal["values", "values_list", "flat", "named"], tuple[str, ...]]
_PROJECTION_FIELDS = ["title", "pages", "price", "published", "author__name", "tags__name"]

# Aggregates use relations the seed populates: every author has books (never NULL), books have
# 0-3 tags (NULL and non-NULL mixed, so NULL ordering is exercised). Combining aggregates over
# two multi-valued relations (tags and author__books) multiplies rows and inflates the values;
# that is intended - both sides of a differential run the same SQL.
# `BookEdition` has a composite primary key, so `Count("editions", distinct=True)` raises
# ValueError in Django; only the plain count is used (it counts the first key column). Every book
# has 1-3 editions (seed), and an `editions__*` filter applied before the annotation shares its
# join, so the count then covers only the editions that passed the filter.
ANNOTATIONS: dict[str, Callable[[], Expression | F]] = {
    "n_tags": lambda: Count("tags", distinct=True),
    "n_editions": lambda: Count("editions"),
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
    deferred: Deferred | None = None  # either only() or defer(), never both
    projection: Projection | None = None  # set by `projected_specs`; no deferral then


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
def _deferrals(draw: st.DrawFn, select_related: tuple[str, ...]) -> Deferred:
    """An only()/defer() call that is valid for `select_related`, except one time in 20.

    Django rejects (FieldError, at compile time) a relation that is both deferred and traversed by
    select_related: defer() naming the FK without any of its fields, or only() that never reaches
    a traversed relation. Both sides of a differential run must raise the same exception, so one
    deferral in 20 (when select_related is not empty) is built to break the rule.
    """
    broken = bool(select_related) and draw(st.integers(0, _INVALID_ONE_IN - 1)) == 0
    related = [f for path in select_related for f in _RELATED_FIELDS[path]]
    # A broken defer() names no field under a traversed FK: with one, deferring the FK stays valid.
    candidates = _BOOK_FIELDS if broken else _BOOK_FIELDS + related
    optional = draw(st.lists(st.sampled_from(candidates), max_size=3, unique=True, min_size=1))
    if draw(st.booleans()):
        extra = list(select_related) if broken else []
        return "defer", tuple(sorted({*optional, *extra}))
    required = [] if broken else [draw(st.sampled_from(_RELATED_FIELDS[p])) for p in select_related]
    return "only", tuple(sorted({*optional, *required}))


@st.composite
def queryset_specs(draw: st.DrawFn) -> QuerySetSpec:
    annotations = tuple(
        sorted(draw(st.lists(st.sampled_from(sorted(ANNOTATIONS)), max_size=2, unique=True)))
    )
    select_related = draw(
        st.lists(st.sampled_from(_SELECT_RELATED), max_size=2, unique=True).map(tuple)
    )
    return QuerySetSpec(
        filters=draw(_lookups(3)),
        excludes=draw(_lookups(1)),
        order_by=draw(_orderings(annotations)),
        select_related=select_related,
        window=draw(st.none() | _window()),
        annotations=annotations,
        deferred=draw(st.none() | _deferrals(select_related)),
    )


@st.composite
def projected_specs(draw: st.DrawFn) -> QuerySetSpec:
    """Generated specs that end in values(), values_list(), flat or named rows."""
    spec = draw(queryset_specs())
    kind = draw(st.sampled_from(["values", "values_list", "flat", "named"]))
    candidates = [*_PROJECTION_FIELDS, *spec.annotations]
    size = 1 if kind == "flat" else draw(st.integers(1, 3))
    fields = tuple(
        draw(st.lists(st.sampled_from(candidates), min_size=size, max_size=size, unique=True))
    )
    return replace(spec, deferred=None, projection=(kind, fields))


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


def filtered_spec(
    filters: tuple[tuple[str, Any], ...],
    annotations: tuple[str, ...] = (),
    order_by: tuple[str, ...] = (),
) -> QuerySetSpec:
    """A spec with filters, annotations and ordering only (example-based tests)."""
    return QuerySetSpec(
        filters=filters,
        excludes=(),
        order_by=order_by,
        select_related=(),
        window=None,
        annotations=tuple(sorted(annotations)),
    )


def deferred_spec(
    mode: Literal["only", "defer"],
    fields: tuple[str, ...],
    *,
    select_related: tuple[str, ...] = (),
    annotations: tuple[str, ...] = (),
    order_by: tuple[str, ...] = (),
) -> QuerySetSpec:
    """A spec shaped like the generated ones, with a fixed only()/defer() (example-based tests)."""
    return QuerySetSpec(
        filters=(),
        excludes=(),
        order_by=order_by,
        select_related=select_related,
        window=None,
        annotations=tuple(sorted(annotations)),
        deferred=(mode, fields),
    )


def is_valid_deferral(spec: QuerySetSpec) -> bool:
    """False when the spec defers a relation that select_related traverses (a FieldError).

    A select_related path traverses every relation on the way (`author__profile` traverses
    `author` too). defer() breaks a traversed FK only when it names the FK and none of its fields;
    only() breaks it unless some named field lies under every traversed relation.
    """
    if spec.deferred is None:
        return True
    mode, fields = spec.deferred
    traversed = {
        "__".join(parts[:depth])
        for parts in (path.split("__") for path in spec.select_related)
        for depth in range(1, len(parts) + 1)
    }
    reached = {path for field in fields for path in traversed if field.startswith(f"{path}__")}
    return not ((set(fields) & traversed) - reached) if mode == "defer" else reached == traversed


def expected_error(spec: QuerySetSpec) -> str | None:
    """The error name a differential outcome of `spec` must carry (None: it must succeed)."""
    return None if is_valid_deferral(spec) else error_name(FieldError())


def valid_deferred_specs() -> st.SearchStrategy[QuerySetSpec]:
    """Generated specs that carry an only()/defer() Django accepts."""
    return queryset_specs().filter(
        lambda spec: spec.deferred is not None and is_valid_deferral(spec)
    )


def expected_deferred_loading(spec: QuerySetSpec) -> tuple[frozenset[str], bool]:
    """`query.deferred_loading` a QuerySet built from `spec` must carry: (names, defer flag)."""
    mode, fields = spec.deferred or ("defer", ())
    return frozenset(fields), mode == "defer"


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
    if spec.deferred:
        mode, fields = spec.deferred
        qs = qs.only(*fields) if mode == "only" else qs.defer(*fields)
    if spec.projection:
        kind, fields = spec.projection
        qs = {
            "values": lambda: qs.values(*fields),
            "values_list": lambda: qs.values_list(*fields),
            "flat": lambda: qs.values_list(*fields, flat=True),
            "named": lambda: qs.values_list(*fields, named=True),
        }[kind]()
    qs = qs.order_by(*spec.order_by, "pk")
    if spec.window:
        qs = qs[spec.window[0] : spec.window[1]]
    return qs
