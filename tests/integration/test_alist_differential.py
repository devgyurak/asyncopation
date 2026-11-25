"""`aq(qs).alist()` matches the sync ORM: values, types, order and exceptions (issue #6, PR A)."""

from functools import partial

import pytest
from hypothesis import given

from harness.catalog.models import Author, Book, BookEdition, FieldZoo
from tests.support.alist_probe import (
    alist_row_count_and_statements,
    alist_statements,
    alist_sync_connection_attempts,
)
from tests.support.backends import XFAIL_UNTIL_ASYNCMY
from tests.support.strategies import (
    QuerySetSpec,
    annotated_spec,
    build_queryset,
    deferred_spec,
    expected_error,
    queryset_specs,
)

pytestmark = XFAIL_UNTIL_ASYNCMY

MODEL_BUILDS = [
    pytest.param(Book.objects.all, id="all"),
    pytest.param(partial(Book.objects.filter, published=True), id="filter"),
    pytest.param(FieldZoo.objects.all, id="field-zoo-from-db-value"),
    pytest.param(lambda: Book.objects.order_by("pk")[:3], id="slice-head"),
    pytest.param(lambda: Book.objects.order_by("pk")[2:5], id="slice-window"),
    pytest.param(
        lambda: Book.objects.filter(tags__isnull=False).distinct().order_by("pk"), id="distinct"
    ),
    pytest.param(
        lambda: Book.objects.extra(select={"double_pages": "pages * 2"}).order_by("pk"),
        id="extra-select",
    ),
    pytest.param(
        lambda: Book.objects.extra(select={"a": "1", "b": "'x'"}).order_by("-pk")[:5],
        id="extra-select-two-columns-sliced",
    ),
    pytest.param(
        lambda: Book.objects.select_related("author__publisher").order_by("pk")[:15],
        id="select-related",
    ),
    pytest.param(
        lambda: Author.objects.select_related("profile", "publisher").order_by("pk"),
        id="select-related-nullable",
    ),
    pytest.param(lambda: Book.objects.only("title").order_by("pk"), id="only"),
    pytest.param(lambda: Book.objects.defer("metadata").order_by("pk"), id="defer"),
    pytest.param(
        lambda: Book.objects.select_related("author").only("title", "author__name").order_by("pk"),
        id="only-select-related",
    ),
    pytest.param(
        partial(build_queryset, annotated_spec(("n_tags",), ("-n_tags", "pk"))), id="annotate"
    ),
    pytest.param(
        partial(build_queryset, deferred_spec("only", ("title",), annotations=("n_tags",))),
        id="only-with-annotation",
    ),
    pytest.param(partial(Book.objects.filter, title="missing"), id="no-rows"),
    pytest.param(BookEdition.objects.all, id="composite-pk-all"),
    pytest.param(
        lambda: BookEdition.objects.select_related("book").order_by("pk")[:20],
        id="composite-pk-select-related",
    ),
    pytest.param(
        lambda: BookEdition.objects.filter(pk__in=[(1, 1), (2, 1), (3, 2)]).order_by("pk"),
        id="composite-pk-in",
    ),
    pytest.param(
        lambda: BookEdition.objects.filter(pk__gt=(0, 0)).order_by("pk"), id="composite-pk-gt"
    ),
]

VALUE_BUILDS = [
    pytest.param(lambda: Book.objects.values().order_by("pk"), id="values-all"),
    pytest.param(lambda: Book.objects.values("title", "price").order_by("pk"), id="values"),
    pytest.param(
        lambda: Book.objects.values_list("title", "pages").order_by("pk"), id="values-list"
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", flat=True).order_by("pk"), id="values-list-flat"
    ),
    pytest.param(
        lambda: Book.objects.values_list("metadata", flat=True).order_by("pk"),
        id="values-list-flat-json",
    ),
    pytest.param(
        lambda: Book.objects.values_list("title", "pages", named=True).order_by("pk"),
        id="values-list-named",
    ),
    pytest.param(lambda: Book.objects.dates("published_at", "month"), id="dates"),
    pytest.param(lambda: Book.objects.datetimes("published_at", "day"), id="datetimes"),
    pytest.param(lambda: BookEdition.objects.values().order_by("pk"), id="composite-pk-values"),
    pytest.param(
        lambda: BookEdition.objects.values("pk", "isbn").order_by("pk"),
        id="composite-pk-values-named-pk",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("pk", flat=True).order_by("pk"),
        id="composite-pk-values-list-pk",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("isbn", "pk").order_by("pk"),
        id="composite-pk-values-list-pk-last",
    ),
    pytest.param(
        lambda: BookEdition.objects.values_list("pk", "released").order_by("pk"),
        id="composite-pk-values-list-pk-released",
    ),
]


@pytest.mark.parametrize("build", MODEL_BUILDS)
async def test_alist_of_model_instances_matches_sync(differential, build):
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("build", VALUE_BUILDS)
async def test_alist_of_values_rows_matches_sync(differential, build):
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="books"),
        pytest.param(FieldZoo.objects.all, id="field-zoo"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
    ],
)
async def test_alist_returns_rows_in_the_sync_order_and_not_nothing(differential, build):
    # when
    result = await differential(build)
    # then
    assert len(result.async_outcome.rows) > 0
    assert result.async_outcome.rows == result.sync_outcome.rows


@given(spec=queryset_specs())
async def test_alist_of_random_querysets_matches_sync(differential, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error == expected_error(spec)
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="pk-in-empty"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(lambda: Book.objects.filter(pk__in=[])[:5], id="pk-in-empty-sliced"),
        pytest.param(lambda: Book.objects.order_by("pk")[:0], id="empty-window"),
    ],
)
async def test_alist_that_cannot_match_issues_no_statement(async_env, build):
    # when
    statements = await alist_statements(build)
    # then
    assert statements == []


async def test_alist_of_an_empty_filter_returns_an_empty_list(differential):
    # when
    result = await differential(partial(Book.objects.filter, pk__in=[]))
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.rows == ()


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(lambda: Book.objects.values("title"), id="values"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Book.objects.order_by("pk")[:3], id="sliced"),
    ],
)
async def test_alist_issues_exactly_one_statement(async_env, build):
    # when
    rows, statements = await alist_row_count_and_statements(build)
    # then
    assert rows > 0
    assert statements == 1


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(FieldZoo.objects.all, id="field-zoo"),
        pytest.param(lambda: Book.objects.select_related("author__publisher"), id="select-related"),
        pytest.param(lambda: Book.objects.values_list("title", flat=True), id="values-list"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="empty"),
    ],
)
async def test_alist_makes_no_sync_connection_attempt(async_env, build):
    # when
    attempts = await alist_sync_connection_attempts(build)
    # then
    assert attempts == 0
