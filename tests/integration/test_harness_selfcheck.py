"""Phase 0 self-check: the differential harness proves itself against Django core async.

These tests use `reference_differential` (sync ORM vs Django core async API). They must pass
before any Asyncopation code exists; if they fail, the harness - not the library - is broken.
"""

from functools import partial

import pytest
from hypothesis import given

from harness.catalog.models import Author, Book, FieldZoo
from harness.seed import EXPECTED_COUNTS
from tests.support.strategies import QuerySetSpec, build_queryset, queryset_specs


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
        pytest.param(lambda: Book.objects.values("title", "price").order_by("pk"), id="values"),
        pytest.param(
            lambda: Book.objects.values_list("metadata", flat=True).order_by("pk"),
            id="values-list-json",
        ),
        pytest.param(FieldZoo.objects.all, id="field-zoo-types"),
        pytest.param(partial(Book.objects.filter, title="missing"), id="empty"),
    ],
)
async def test_reference_list_matches_sync(readonly_db, reference_differential, build):
    # when
    result = await reference_differential(build)
    # then
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
    assert result.async_outcome == result.sync_outcome


@pytest.mark.xfail(strict=True, reason="Phase 1: asyncopation.aq() / alist() not implemented yet")
async def test_asyncopation_list_matches_sync(readonly_db, differential):
    # given
    build = partial(Book.objects.filter, published=True)
    # when
    result = await differential(build)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.error is None
