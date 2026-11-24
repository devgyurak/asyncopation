"""Phase 0 self-check: the differential harness proves itself against Django core async.

These tests use `reference_differential` (sync ORM vs Django core async API). They must pass
before any Asyncopation code exists; if they fail, the harness - not the library - is broken.
"""

from functools import partial

import pytest
from hypothesis import given

from harness.catalog.models import Author, Book, BookEdition, FieldZoo
from harness.seed import EXPECTED_COUNTS
from tests.support.snapshot import annotation_names, related_snapshot, snapshot
from tests.support.strategies import QuerySetSpec, annotated_spec, build_queryset, queryset_specs


@pytest.mark.parametrize(("model_name", "expected"), list(EXPECTED_COUNTS.items()))
async def test_seed_creates_expected_row_counts(readonly_db, model_name, expected):
    # given
    from django.apps import apps

    model = apps.get_model("catalog", model_name)
    # when
    count = await model.objects.acount()
    # then
    assert count == expected


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all-books"),
        pytest.param(partial(Book.objects.filter, published=True), id="filter"),
        pytest.param(
            lambda: Book.objects.select_related("author__publisher").order_by("-price", "pk")[:15],
            id="select-related-slice",
        ),
        pytest.param(
            lambda: Author.objects.select_related("profile", "publisher").order_by("pk"),
            id="select-related-nullable",
        ),
        pytest.param(lambda: Book.objects.values("title", "price").order_by("pk"), id="values"),
        pytest.param(
            lambda: Book.objects.values_list("metadata", flat=True).order_by("pk"),
            id="values-list-json",
        ),
        pytest.param(FieldZoo.objects.all, id="field-zoo-types"),
        pytest.param(partial(Book.objects.filter, title="missing"), id="empty"),
        pytest.param(BookEdition.objects.all, id="composite-pk-all"),
        pytest.param(lambda: BookEdition.objects.values().order_by("pk"), id="composite-pk-values"),
        pytest.param(
            lambda: BookEdition.objects.values_list("pk", flat=True).order_by("pk"),
            id="composite-pk-values-list-pk",
        ),
        pytest.param(
            lambda: BookEdition.objects.select_related("book").order_by("pk")[:20],
            id="composite-pk-select-related",
        ),
        pytest.param(
            lambda: BookEdition.objects.values("pk", "isbn").order_by("pk"),
            id="composite-pk-values-named-pk",
        ),
        pytest.param(
            lambda: BookEdition.objects.values_list("isbn", "pk").order_by("pk"),
            id="composite-pk-values-list-pk-last",
        ),
        pytest.param(
            lambda: BookEdition.objects.values_list("pk", "released").order_by("pk"),
            id="composite-pk-values-list-pk-released",
        ),
    ],
)
async def test_reference_list_matches_sync(readonly_db, reference_differential, build):
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    ("build", "op"),
    [
        pytest.param(partial(Book.objects.filter, title="missing"), "get", id="get-does-not-exist"),
        pytest.param(Book.objects.all, "get", id="get-multiple"),
        pytest.param(partial(Book.objects.filter, title="book-1"), "get", id="get-one"),
        pytest.param(partial(Book.objects.filter, published=False), "count", id="count"),
        pytest.param(partial(Author.objects.order_by, "-name"), "first", id="first"),
        pytest.param(partial(Book.objects.filter, title="missing"), "exists", id="exists-false"),
        pytest.param(
            partial(BookEdition.objects.filter, book__title="book-0", number=1),
            "get",
            id="composite-pk-get",
        ),
        pytest.param(
            partial(BookEdition.objects.filter, book__title="book-0", number=99),
            "get",
            id="composite-pk-get-does-not-exist",
        ),
        pytest.param(BookEdition.objects.all, "get", id="composite-pk-get-multiple"),
        pytest.param(BookEdition.objects.all, "count", id="composite-pk-count"),
        pytest.param(BookEdition.objects.all, "first", id="composite-pk-first"),
    ],
)
async def test_reference_single_result_operations_match_sync(
    readonly_db, reference_differential, build, op
):
    # when
    result = await reference_differential(build, op)
    # then
    assert result.async_outcome == result.sync_outcome


async def test_differential_reports_errors_by_exception_type(readonly_db, reference_differential):
    # given
    build = partial(Book.objects.filter, title="missing")
    # when
    result = await reference_differential(build, "get")
    # then
    assert result.sync_outcome.error == "harness.catalog.models.Book.DoesNotExist"


@given(spec=queryset_specs())
async def test_reference_random_querysets_match_sync(
    readonly_db, reference_differential, spec: QuerySetSpec
):
    # given
    build = partial(build_queryset, spec)
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(annotated_spec(("n_tags",), ("-n_tags",)), id="count-distinct-ordered"),
        pytest.param(annotated_spec(("tag_id_sum",), ("tag_id_sum",)), id="sum-null-and-values"),
        pytest.param(annotated_spec(("author_pages",), ("-author_pages",)), id="sum-non-null"),
        pytest.param(
            annotated_spec(("author_avg_price",), ("author_avg_price",)), id="avg-decimal"
        ),
        pytest.param(
            annotated_spec(("tag_id_sum_or_zero",), ("-tag_id_sum_or_zero",)),
            id="coalesce-aggregate",
        ),
        pytest.param(annotated_spec(("double_pages",), ("-double_pages",)), id="f-expression"),
        pytest.param(annotated_spec(("length",), ("length", "-pages")), id="case-when"),
        pytest.param(
            annotated_spec(("n_tags", "author_pages", "length"), ("-author_pages",)),
            id="mixed-aggregates",
        ),
    ],
)
async def test_reference_annotated_querysets_match_sync(
    readonly_db, reference_differential, spec: QuerySetSpec
):
    # given
    build = partial(build_queryset, spec)
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome
    assert annotation_names(result.sync_outcome.rows[0]) == spec.annotations


@pytest.mark.xfail(strict=True, reason="Phase 1: asyncopation.aq() / alist() not implemented yet")
async def test_asyncopation_list_matches_sync(readonly_db, differential):
    # given
    build = partial(Book.objects.filter, published=True)
    # when
    result = await differential(build)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.error is None


async def test_select_related_snapshot_contains_nested_related_objects(readonly_db):
    # given
    queryset = Book.objects.select_related("author__publisher").filter(
        author__publisher__isnull=False
    )
    book = await queryset.aearliest("pk")
    # when
    publisher = related_snapshot(snapshot(book), "author", "publisher")
    # then
    assert publisher is not None
    assert publisher[0] == "catalog.Publisher"
