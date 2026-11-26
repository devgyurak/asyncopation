"""QuerySet cases of the harness catalog with fixed rows, for the engine stages without a database.

Every case has a builder (taking the connection alias, so each test compiles in its own alias) and
the rows a PostgreSQL server would return for its compiled SELECT (column order = select order).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from django.db.models import FETCH_PEERS, FETCH_RAISE, F, QuerySet  # ty: ignore[missing-module-member] - missing from django-types

from harness.catalog.models import Author, Book
from tests.support.seeded import books_manager_of

_KNOWN_AUTHORS: dict[str, Author] = {}


def known_author(alias: str) -> Author:
    """An unsaved author (pk 10) per alias: the known related object of its related manager.

    One per alias (the tests assert on its identity). Assigning it to a book records the alias on
    its `_state.db`, which a later related manager would use as a routing hint, so every call
    clears that again.
    """
    author = _KNOWN_AUTHORS.setdefault(alias, Author(pk=10, name="known"))
    author._state.db = None
    return author


type Builder = Callable[[str], QuerySet[Any]]
type Rows = list[tuple[Any, ...]]

STAMP = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

BOOK_ROWS: Rows = [
    (1, "alpha", 10, Decimal("9.50"), 120, True, STAMP, '{"k": 1}'),
    (2, "beta", 11, Decimal("0.00"), 0, False, None, "{}"),
    (3, "gamma", 10, Decimal("12.34"), 999, True, STAMP, "[1, 2]"),
]
AUTHOR_COLUMNS: Rows = [
    (10, "ann", "ann@example.com", date(1980, 5, 6), 5),
    (11, "bob", "bob@example.com", None, None),
    (10, "ann", "ann@example.com", date(1980, 5, 6), 5),
]
PUBLISHER_COLUMNS: Rows = [(5, "press", "KR"), (None, None, None), (5, "press", "KR")]


def _joined(left: Rows, right: Rows) -> Rows:
    return [(*a, *b) for a, b in zip(left, right, strict=True)]


def _with_extra(rows: Rows, values: list[Any]) -> Rows:
    return [(*row, value) for row, value in zip(rows, values, strict=True)]


def _author_rows() -> Rows:
    return [
        (1, "ann", "ann@example.com", date(1980, 5, 6), 5),
        (2, "bob", "bob@example.com", None, None),
    ]


QUERYSETS: dict[str, Builder] = {
    "book_all": lambda a: Book.objects.using(a).all(),
    "book_select_related_author": lambda a: Book.objects.using(a).select_related("author"),
    "book_annotated": lambda a: Book.objects.using(a).annotate(double_pages=F("pages") * 2),
    "book_only_title": lambda a: Book.objects.using(a).only("title"),
    "book_peers": lambda a: Book.objects.using(a).fetch_mode(FETCH_PEERS),
    "book_only_raise": lambda a: Book.objects.using(a).only("title").fetch_mode(FETCH_RAISE),
    "author_select_related_publisher": lambda a: Author.objects.using(a).select_related(
        "publisher"
    ),
    "values": lambda a: Book.objects.using(a).values("title", "pages"),
    "values_list": lambda a: Book.objects.using(a).values_list("title", "pages"),
    "values_list_flat": lambda a: Book.objects.using(a).values_list("title", flat=True),
    "values_list_named": lambda a: Book.objects.using(a).values_list("title", "pages", named=True),
    "author_books": lambda a: books_manager_of(known_author(a)).using(a).all(),
    "author_books_select_related": lambda a: (
        books_manager_of(known_author(a)).using(a).select_related("author")
    ),
    "author_books_only_title": lambda a: books_manager_of(known_author(a)).using(a).only("title"),
    "book_empty": lambda a: Book.objects.using(a).filter(pk__in=[]),
    "book_filtered": lambda a: Book.objects.using(a).filter(pages=5, published=True),
    "book_for_update": lambda a: Book.objects.using(a).select_for_update(),
    "book_extra_select": lambda a: Book.objects.using(a).extra(select={"one": "1"}),
    "book_union_values_unselected_order": lambda a: (
        Book.objects.using(a)
        .filter(pk__lt=5)
        .union(Book.objects.using(a).filter(pk__gt=40))
        .values("title")
        .order_by("pages")
    ),
    "book_union_named_unselected_order": lambda a: (
        Book.objects.using(a)
        .filter(pk__lt=5)
        .union(Book.objects.using(a).filter(pk__gt=40))
        .values_list("title", named=True)
        .order_by("pages")
    ),
    "book_values_extra": lambda a: (
        Book.objects.using(a).extra(select={"x": "pages * 2"}).values("title", "x")
    ),
    "book_ordering_column": lambda a: (
        Book.objects.using(a).values("title").distinct().order_by("pages")
    ),
}

ROWS: dict[str, Rows] = {
    "book_all": BOOK_ROWS,
    "book_select_related_author": _joined(BOOK_ROWS, AUTHOR_COLUMNS),
    "book_annotated": _with_extra(BOOK_ROWS, [240, 0, 1998]),
    "book_only_title": [(1, "alpha"), (2, "beta")],
    "book_peers": BOOK_ROWS,
    "book_only_raise": [(1, "alpha"), (2, "beta")],
    "author_select_related_publisher": _joined(_author_rows(), PUBLISHER_COLUMNS[:2]),
    "author_books": BOOK_ROWS,
    "author_books_select_related": _joined(BOOK_ROWS, AUTHOR_COLUMNS),
    "author_books_only_title": [(1, "alpha"), (2, "beta")],
    "values": [("alpha", 120), ("beta", 0)],
    "values_list": [("alpha", 120), ("beta", 0)],
    "values_list_flat": [("alpha",), ("beta",)],
    "values_list_named": [("alpha", 120), ("beta", 0)],
}
