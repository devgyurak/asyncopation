"""Query cases for the core differential tests: each builds a fresh Django `Query` (and the
aggregate expressions) so that no side shares state with another."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.db.models import (
    Avg,
    Count,
    F,
    Func,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Subquery,
    Sum,
    Value,
    Window,
)
from django.db.models.functions import RowNumber
from django.db.models.lookups import GreaterThan

from harness.catalog.models import Author, Book, BookEdition, FieldZoo

type QueryBuilder = Callable[[], Any]

QUERIES: dict[str, QueryBuilder] = {
    "all": lambda: Book.objects.all().query,
    "filter": lambda: Book.objects.filter(published=True).query,
    "annotate": lambda: Book.objects.annotate(n=Count("tags")).query,
    "annotate-filter-on-annotation": lambda: (
        Book.objects.annotate(n=Count("tags")).filter(n__gt=1).query
    ),
    "slice": lambda: Book.objects.order_by("pk")[2:7].query,
    "slice-head": lambda: Book.objects.order_by("-pages")[:5].query,
    "distinct": lambda: Book.objects.filter(tags__isnull=False).distinct().query,
    "values-distinct": lambda: Book.objects.values("author").distinct().query,
    "union": lambda: Book.objects.filter(pk__lt=10).union(Book.objects.filter(pk__gt=5)).query,
    "empty-in": lambda: Book.objects.filter(pk__in=[]).query,
    "empty-none": lambda: Book.objects.none().query,
    "no-match": lambda: Book.objects.filter(title="missing").query,
    "composite-pk": lambda: BookEdition.objects.all().query,
    "composite-pk-filter": lambda: BookEdition.objects.filter(pk__gt=(0, 0)).query,
    "select-related": lambda: Book.objects.select_related("author__publisher").query,
    "ordered": lambda: Book.objects.order_by("-price", "pk").query,
    "annotate-expression": lambda: Book.objects.annotate(d=F("pages") * 2).query,
    "annotate-window": lambda: Book.objects.annotate(r=Window(RowNumber(), order_by=F("pk"))).query,
    "values-grouped": lambda: Book.objects.values("author").annotate(n=Count("pk")).query,
    "values-grouped-trailing-annotation": lambda: (
        Book.objects.values("author").annotate(n=Count("pk")).annotate(w=F("pages") + 1).query
    ),
    "annotate-aggregate-and-column": lambda: (
        Book.objects.annotate(n=Count("tags"), w=F("pages") + 1).query
    ),
    "values-of-an-annotation-grouped": lambda: (
        Book.objects.annotate(x=F("pages") % 7).values("x").annotate(n=Count("pk")).query
    ),
    "annotated-union": lambda: (
        Book.objects.annotate(n=Count("tags"))
        .union(Book.objects.annotate(n=Count("tags")).filter(pk__lt=5))
        .query
    ),
    # An aggregate used directly as a condition: the WHERE clause moves to HAVING.
    "filter-on-an-aggregate-expression": lambda: (
        Book.objects.filter(GreaterThan(Count("tags"), 1)).query
    ),
    # Rows are counted through a subquery whose columns come from the annotated `values()`.
    "union-of-annotated-values": lambda: (
        Book.objects.annotate(x=Value(1))
        .union(Book.objects.annotate(x=Value(2)))
        .values("title", "x")
        .query
    ),
    "distinct-slice-ordered-by-an-expression": lambda: (
        Book.objects.distinct().order_by(F("pages") * -1, "pk")[:5].query
    ),
    "slice-ordered-by-an-extra-select": lambda: (
        Book.objects.extra(select={"x": "pages + 1"}).order_by("x")[:5].query
    ),
    "subquery-annotation": lambda: Book.objects.annotate(author_name=_author_name()).query,
}


def _author_name() -> Subquery:
    return Subquery(Author.objects.filter(pk=OuterRef("author_id")).values("name")[:1])


class GenerateSeries(Func):
    """A set-returning function (PostgreSQL): one row of the QuerySet per value it yields."""

    function = "generate_series"
    set_returning = True
    output_field = IntegerField()


# PostgreSQL only: `generate_series` is not a MySQL / MariaDB function.
POSTGRESQL_QUERIES: dict[str, QueryBuilder] = {
    "set-returning-annotation": lambda: (
        Book.objects.filter(pk__lte=3).annotate(g=GenerateSeries(Value(1), Value(2))).query
    ),
}

type ExprBuilder = Callable[[], dict[str, Any]]

AGGREGATES: dict[str, ExprBuilder] = {
    "sum-decimal": lambda: {"total": Sum("price")},
    "avg-decimal": lambda: {"mean": Avg("price")},
    "max-datetime": lambda: {"latest": Max("published_at")},
    "min-datetime": lambda: {"earliest": Min("published_at")},
    "max-int": lambda: {"most": Max("pages")},
    "count-field": lambda: {"n": Count("title")},
    # No `empty_result_set_value` (NotImplemented): over an empty set the statement still runs.
    "sum-plus-one": lambda: {"total": Sum("price") + Value(1)},
    # An aggregate that refers to an earlier one of the same call (resolved against each other).
    "ratio-of-aggregates": lambda: {"total": Sum("pages"), "per_book": F("total") / Count("pk")},
    "many": lambda: {
        "total": Sum("price"),
        "mean": Avg("pages"),
        "latest": Max("published_at"),
        "n": Count("pk"),
    },
}

QUERIES_FOR_AGGREGATES: dict[str, QueryBuilder] = {
    "all": QUERIES["all"],
    "filter": QUERIES["filter"],
    "slice": QUERIES["slice"],
    "distinct": QUERIES["distinct"],
    "annotate": QUERIES["annotate"],
    "empty-in": QUERIES["empty-in"],
    "no-match": QUERIES["no-match"],
}

# (query, aggregates): aggregates that refer to an annotation of the query, or queries whose
# annotations decide whether the aggregation needs a subquery and which columns it keeps.
ANNOTATION_AGGREGATES: dict[str, tuple[QueryBuilder, ExprBuilder]] = {
    "sum-of-an-expression-annotation": (
        QUERIES["annotate-expression"],
        lambda: {"s": Sum("d")},
    ),
    "max-of-a-count-annotation": (QUERIES["annotate"], lambda: {"m": Max("n")}),
    "max-of-a-window-annotation": (QUERIES["annotate-window"], lambda: {"m": Max("r")}),
    "sum-over-a-grouped-query": (QUERIES["values-grouped"], lambda: {"s": Sum("n")}),
    "sum-over-a-grouped-query-with-a-trailing-annotation": (
        QUERIES["values-grouped-trailing-annotation"],
        lambda: {"s": Sum("n")},
    ),
    "max-of-a-subquery-annotation": (
        QUERIES["subquery-annotation"],
        lambda: {"m": Max("author_name")},
    ),
    "max-of-a-grouped-annotation": (
        QUERIES["values-of-an-annotation-grouped"],
        lambda: {"m": Max("n")},
    ),
    "max-with-an-aggregate-and-a-column-annotation": (
        QUERIES["annotate-aggregate-and-column"],
        lambda: {"m": Max("n")},
    ),
}

POSTGRESQL_ANNOTATION_AGGREGATES: dict[str, tuple[QueryBuilder, ExprBuilder]] = {
    "count-over-a-set-returning-annotation": (
        POSTGRESQL_QUERIES["set-returning-annotation"],
        lambda: {"n": Count("pk")},
    ),
}

ZOO_AGGREGATES: dict[str, ExprBuilder] = {
    "zoo-decimal": lambda: {"total": Sum("decimal")},
    "zoo-float": lambda: {"largest": Max("floating")},
    "zoo-datetime": lambda: {"latest": Max("datetime")},
    "zoo-date": lambda: {"earliest": Min("date")},
    "zoo-duration": lambda: {"longest": Max("duration")},
}


def zoo_query() -> Any:
    return FieldZoo.objects.all().query


# Aliases Django rejects with a ValueError (`Query.check_alias`): whitespace, quotes, a semicolon
# or an SQL comment. `get_aggregation` checks them first, before anything is compiled.
INVALID_ALIAS_AGGREGATES: dict[str, ExprBuilder] = {
    "alias-with-a-space": lambda: {"x y": Sum("price")},
    "alias-with-a-quote": lambda: {'x"y': Max("pages")},
    "alias-with-a-comment": lambda: {"x--y": Count("pk")},
}

# Aggregates over the composite primary key of `BookEdition`: Django 6.1 rejects `Max('pk')` with a
# ValueError (the cores must raise the same error) and counts the first column of the key. No
# aggregate returns a composite value in 6.1, so the `composite_fields_to_tuples` step of
# `finish_aggregation` (ported as is) cannot be reached by a differential case.
COMPOSITE_PK_AGGREGATES: dict[str, ExprBuilder] = {
    "max-composite-pk": lambda: {"m": Max("pk")},
    "count-composite-pk": lambda: {"n": Count("pk")},
}


def composite_pk_query() -> Any:
    return BookEdition.objects.all().query
