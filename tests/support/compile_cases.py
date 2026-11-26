"""Query builders (and a timezone-override runner) for the compile-only SQL identity sweep.

Each builder returns a fresh ``Query`` (never a QuerySet) so it can be compiled by both wrappers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.db import connections
from django.db.models import CharField, Exists, F, Model, OuterRef, Subquery, UUIDField
from django.db.models.constants import OnConflict
from django.db.models.functions import Cast, ExtractYear, TruncDate
from django.db.models.sql import InsertQuery, UpdateQuery
from django.test import override_settings

from harness.catalog.models import Author, Book, FieldZoo
from tests.support.compile_differential import (
    Comparison,
    compare_compilation,
    compile_wrapper_of,
)
from tests.support.compile_wrappers import forget_compile_wrapper, unique_alias

TIMEZONE_SETTINGS: dict[str, Any] = {"USE_TZ": True, "TIME_ZONE": "Asia/Seoul"}
# Without USE_TZ the connection time zone is TIME_ZONE (with it, UTC): restoring must not keep it.
NAIVE_SETTINGS: dict[str, Any] = {"USE_TZ": False, "TIME_ZONE": "Asia/Seoul"}


def title_contains_author_name() -> Any:
    return Book.objects.filter(title__contains=F("author__name")).query


def title_icontains_author_name() -> Any:
    return Book.objects.filter(title__icontains=F("author__name")).query


def title_startswith_author_name() -> Any:
    return Book.objects.filter(title__startswith=F("author__name")).query


def title_endswith_author_name() -> Any:
    return Book.objects.filter(title__endswith=F("author__name")).query


def title_istartswith_author_name() -> Any:
    return Book.objects.filter(title__istartswith=F("author__name")).query


def title_iendswith_author_name() -> Any:
    return Book.objects.filter(title__iendswith=F("author__name")).query


def title_contains_literal_with_wildcards() -> Any:
    return Book.objects.filter(title__contains="50%_off\\").query


def title_regex() -> Any:
    return Book.objects.filter(title__regex=r"^book-[0-9]+$").query


def title_iregex() -> Any:
    return Book.objects.filter(title__iregex=r"^BOOK-[0-9]+$").query


def title_iexact() -> Any:
    return Book.objects.filter(title__iexact="Book-1").query


def published_date_lookup() -> Any:
    return Book.objects.filter(published_at__date="2024-01-02").query


def published_trunc_date() -> Any:
    return Book.objects.annotate(day=TruncDate("published_at")).filter(day="2024-01-02").query


def published_extract_year() -> Any:
    return Book.objects.annotate(year=ExtractYear("published_at")).filter(year=2024).query


def metadata_key_lookup() -> Any:
    return Book.objects.filter(metadata__key="value").query


def metadata_nested_key_lookup() -> Any:
    return Book.objects.filter(metadata__a__b=1).query


def cast_pages_to_char() -> Any:
    return (
        Book.objects.annotate(text=Cast("pages", CharField(max_length=10))).filter(text="1").query
    )


def cast_pages_to_uuid() -> Any:
    """``Cast(..., UUIDField())``: the column type comes from the wrapper's ``data_types``."""
    return Book.objects.annotate(uid=Cast("pages", UUIDField())).filter(uid=SAMPLE_UUID).query


def subquery_filter() -> Any:
    books = Book.objects.filter(author=OuterRef("pk")).order_by("-pages").values("title")[:1]
    return Author.objects.annotate(top=Subquery(books)).filter(top__startswith="book").query


def exists_filter() -> Any:
    return Author.objects.filter(Exists(Book.objects.filter(author=OuterRef("pk")))).query


def binary_filter() -> Any:
    return FieldZoo.objects.filter(binary=b"abc").query


def binary_insert() -> Any:
    query = InsertQuery(FieldZoo)
    fields = [FieldZoo._meta.get_field(name) for name in ("char", "binary")]
    query.insert_values(fields, [FieldZoo(char="c", binary=b"abc")])
    return query


def binary_update() -> Any:
    query = cast(UpdateQuery, FieldZoo.objects.filter(pk=1).query.chain(UpdateQuery))
    query.add_update_values({"binary": b"abc"})
    return query


SAMPLE_UUID = UUID("12345678-1234-5678-1234-567812345678")
AWARE_MOMENT = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


def uuid_filter() -> Any:
    return FieldZoo.objects.filter(uuid=SAMPLE_UUID).query


def aware_datetime_filter() -> Any:
    return FieldZoo.objects.filter(datetime=AWARE_MOMENT).query


def decimal_filter() -> Any:
    return FieldZoo.objects.filter(decimal=Decimal("12.345678")).query


def duration_filter() -> Any:
    return FieldZoo.objects.filter(duration=timedelta(days=1, seconds=5, microseconds=7)).query


def json_contains_filter() -> Any:
    return FieldZoo.objects.filter(json__contains={"a": 1}).query


def json_has_key_filter() -> Any:
    return FieldZoo.objects.filter(json__has_key="a").query


def json_contained_by_filter() -> Any:
    return FieldZoo.objects.filter(json__contained_by={"a": 1, "b": 2}).query


def _bulk_insert(**options: Any) -> Any:
    query = InsertQuery(FieldZoo, **options)
    fields = [FieldZoo._meta.get_field(name) for name in ("char", "uuid", "decimal")]
    rows: list[Model] = [FieldZoo(char="c", uuid=SAMPLE_UUID, decimal=Decimal("1.5"))]
    query.insert_values(fields, rows)
    return query


def bulk_insert_ignore_conflicts() -> Any:
    return _bulk_insert(on_conflict=OnConflict.IGNORE)


def bulk_insert_update_conflicts() -> Any:
    return _bulk_insert(
        on_conflict=OnConflict.UPDATE,
        update_fields=[FieldZoo._meta.get_field("char")],
        unique_fields=[FieldZoo._meta.pk],
    )


def update_filtered_through_a_join() -> Any:
    query = cast(UpdateQuery, Book.objects.filter(author__name="author-1").query.chain(UpdateQuery))
    query.add_update_values({"title": "t"})
    return query


def compare_aware_lookup_after_naive_override(build: Any) -> Comparison:
    """Compile `build()` after the shared wrapper cached its time zone under ``USE_TZ=False``.

    Django keeps a connection's ``timezone`` per ``USE_TZ``; an aware value becomes naive in UTC
    when ``USE_TZ`` is on and the *current* zone is Asia/Seoul, so a wrapper that kept the naive
    ``None`` would adapt the value in Seoul time.
    """
    real: Any = connections["default"]
    forget_compile_wrapper("default")  # an earlier case may have cached the zone already
    with override_settings(**NAIVE_SETTINGS):
        compile_wrapper_of(real, "default").timezone  # noqa: B018 - fills the cached property
    with override_settings(**TIMEZONE_SETTINGS):
        return compare_compilation(build)


def compare_under_timezone_override(build: Any) -> Comparison:
    """Compile `build()` while ``USE_TZ``/``TIME_ZONE`` are overridden to Asia/Seoul."""
    with override_settings(**TIMEZONE_SETTINGS):
        return compare_compilation(build)


def compare_after_timezone_override(build: Any) -> Comparison:
    """Compile `build()` once with a naive Asia/Seoul connection time zone, then again after the
    settings were restored."""
    forget_compile_wrapper("default")  # built anew under the naive settings
    with override_settings(**NAIVE_SETTINGS):
        compare_compilation(build)
    return compare_compilation(build)


def timezone_names_after_naive_override() -> tuple[str, str]:
    """``timezone_name`` of the real wrapper and of a compile-only wrapper registered under a
    fresh alias (so it starts with empty caches), once the settings that were overridden while the
    latter was read have been restored."""
    real: Any = connections["default"]
    probe = unique_alias()
    try:
        with override_settings(**NAIVE_SETTINGS):
            compile_wrapper_of(real, probe).timezone_name  # noqa: B018 - fills the cached property
        return real.timezone_name, compile_wrapper_of(real, probe).timezone_name
    finally:
        forget_compile_wrapper(probe)
