"""QuerySet builders shared by the integration and unit tests of the read APIs (`acount`, `aexists`,
`afirst`, `aget`). Each call builds a fresh QuerySet so that no test shares state with another."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Window
from django.db.models.functions import RowNumber

from harness.catalog.models import Author, Book, Tag


def extra_ordered() -> Any:
    return Book.objects.extra(select={"x": "pages + 1"}).order_by("x")


def extra_values() -> Any:
    return Book.objects.extra(select={"x": "pages + 1"}).values("x", "title")


def extra_values_list_ordered() -> Any:
    return Book.objects.extra(select={"x": "pages + 1"}).order_by("x").values_list("x", flat=True)


def values_annotated() -> Any:
    return Book.objects.values("author").annotate(n=Count("pk"))


def alias_filtered() -> Any:
    return Book.objects.alias(n=Count("tags")).filter(n__gt=1)


def annotated_expression() -> Any:
    return Book.objects.annotate(d=F("pages") * 2)


def window_filtered() -> Any:
    return Book.objects.annotate(r=Window(RowNumber(), order_by=F("pk"))).filter(r__lte=3)


def exists_annotated() -> Any:
    return Book.objects.annotate(tagged=Exists(Tag.objects.filter(books=OuterRef("pk"))))


def subquery_annotated() -> Any:
    names = Author.objects.filter(pk=OuterRef("author_id")).values("name")[:1]
    return Book.objects.annotate(author_name=Subquery(names))


def subquery_filtered() -> Any:
    return Book.objects.filter(Q(pk__in=Book.objects.filter(pages__gt=500).values("pk")))


def locked_sliced() -> Any:
    return Book.objects.select_for_update()[:5]


def distinct_on_title() -> Any:
    return Book.objects.order_by("title").distinct("title")


def unordered() -> Any:
    return Book.objects.order_by()
