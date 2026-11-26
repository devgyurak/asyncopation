"""Phase 0 self-check: the differential harness proves itself against Django core async.

These tests use `reference_differential` (sync ORM vs Django core async API). They must pass
before any Asyncopation code exists; if they fail, the harness - not the library - is broken.
"""

from functools import partial

import pytest
from django.core.exceptions import FieldError
from django.db.models import FETCH_ONE, FETCH_PEERS  # ty: ignore[missing-module-member] - missing from django-types
from hypothesis import given

from harness.catalog.models import Author, Book, BookEdition, FieldZoo
from harness.seed import EXPECTED_COUNTS
from tests.support.backends import XFAIL_UNTIL_ASYNCMY
from tests.support.deferred_queries import (
    count_deferred_markers,
    deferred_field_names,
    evaluate_and_snapshot_query_counts,
)
from tests.support.differential import error_name
from tests.support.snapshot import annotation_names, related_snapshot, snapshot
from tests.support.strategies import (
    QuerySetSpec,
    annotated_spec,
    build_queryset,
    deferred_spec,
    expected_error,
    filtered_spec,
    is_valid_deferral,
    queryset_specs,
    valid_deferred_specs,
)

LIST_BUILDS = [
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
]


SINGLE_OPERATIONS = [
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
]


@pytest.mark.parametrize(("model_name", "expected"), list(EXPECTED_COUNTS.items()))
async def test_seed_creates_expected_row_counts(readonly_db, model_name, expected):
    # given
    from django.apps import apps

    model = apps.get_model("catalog", model_name)
    # when
    count = await model.objects.acount()
    # then
    assert count == expected


@pytest.mark.parametrize("build", LIST_BUILDS)
async def test_reference_list_matches_sync(readonly_db, reference_differential, build):
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(("build", "op"), SINGLE_OPERATIONS)
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
    assert result.sync_outcome.error == expected_error(spec)
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
        pytest.param(annotated_spec(("n_editions",), ("n_editions",)), id="editions-count-asc"),
        pytest.param(
            annotated_spec(("n_editions",), ("-n_editions", "pk")), id="editions-count-desc"
        ),
        pytest.param(
            annotated_spec(("n_tags", "n_editions"), ("-n_editions", "-n_tags")),
            id="tags-and-editions-multiply-rows",
        ),
        pytest.param(
            filtered_spec(
                (("editions__released__isnull", False),), ("n_editions",), ("n_editions",)
            ),
            id="editions-filter-shares-count-join",
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


@pytest.mark.parametrize("released_is_null", [True, False], ids=["null", "set"])
async def test_reference_editions_filter_matches_sync(
    readonly_db, reference_differential, released_is_null
):
    # given
    spec = filtered_spec((("editions__released__isnull", released_is_null),))
    # when
    result = await reference_differential(partial(build_queryset, spec))
    # then
    assert result.sync_outcome.error is None
    assert len(result.sync_outcome.rows) > 0
    assert result.async_outcome == result.sync_outcome


@XFAIL_UNTIL_ASYNCMY
async def test_asyncopation_list_matches_sync(readonly_db, differential):
    # given
    build = partial(Book.objects.filter, published=True)
    # when
    result = await differential(build)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.error is None


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("build", LIST_BUILDS)
async def test_asyncopation_list_of_the_reference_builds_matches_sync(
    readonly_db, differential, build
):
    # when
    result = await differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize(("build", "op"), SINGLE_OPERATIONS)
async def test_asyncopation_single_result_operations_match_sync(
    readonly_db, differential, build, op
):
    # when
    result = await differential(build, op)
    # then
    assert result.async_outcome == result.sync_outcome


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


DEFERRED_BUILDS = [
    pytest.param(lambda: Book.objects.only("title").order_by("pk"), id="only"),
    pytest.param(lambda: Book.objects.defer("metadata").order_by("pk"), id="defer"),
    pytest.param(
        lambda: Book.objects.select_related("author").only("title", "author__name").order_by("pk"),
        id="only-select-related",
    ),
    pytest.param(lambda: BookEdition.objects.only("number").order_by("pk"), id="only-composite-pk"),
    pytest.param(
        partial(build_queryset, deferred_spec("only", ("title",), annotations=("n_tags",))),
        id="only-with-annotation",
    ),
    pytest.param(
        partial(build_queryset, deferred_spec("defer", ("pages",), order_by=("-pages",))),
        id="defer-the-ordering-field",
    ),
    pytest.param(
        lambda: Book.objects.only("title", "price").defer("price").order_by("pk"),
        id="only-then-defer",
    ),
    pytest.param(
        partial(build_queryset, deferred_spec("only", ("title",), annotations=("n_editions",))),
        id="only-with-editions-count",
    ),
    pytest.param(
        partial(
            build_queryset,
            deferred_spec("only", ("title", "author__name"), select_related=("author",)),
        ),
        id="spec-only-select-related",
    ),
    pytest.param(
        partial(
            build_queryset,
            deferred_spec(
                "defer", ("author__email", "price"), select_related=("author", "author__profile")
            ),
        ),
        id="spec-defer-related-field",
    ),
    pytest.param(
        partial(
            build_queryset,
            deferred_spec(
                "only",
                ("title", "author__name", "author__publisher__country"),
                select_related=("author", "author__publisher"),
            ),
        ),
        id="spec-only-nested-select-related",
    ),
]


@pytest.mark.parametrize("build", DEFERRED_BUILDS)
async def test_reference_deferred_querysets_match_sync(readonly_db, reference_differential, build):
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("fetch_mode", [FETCH_ONE, FETCH_PEERS], ids=["fetch-one", "fetch-peers"])
@pytest.mark.parametrize("build", DEFERRED_BUILDS)
def test_snapshot_of_deferred_querysets_runs_no_extra_query(readonly_db, build, fetch_mode):
    # when
    rows, evaluated, snapshotted = evaluate_and_snapshot_query_counts(build, fetch_mode)
    # then
    assert (rows > 0, evaluated, snapshotted) == (True, 1, 0)


@pytest.mark.parametrize("build", DEFERRED_BUILDS)
def test_snapshot_of_deferred_querysets_marks_deferred_fields(readonly_db, build):
    # when
    markers = count_deferred_markers(build)
    # then
    assert markers > 0


VALUES_OF_DEFERRED = [
    pytest.param(
        lambda: Book.objects.only("title").values("pk", "title").order_by("pk"), id="only-values"
    ),
    pytest.param(
        lambda: Book.objects.defer("metadata").values_list("pk", "metadata").order_by("pk"),
        id="defer-values-list",
    ),
]


@pytest.mark.parametrize("build", VALUES_OF_DEFERRED)
async def test_reference_values_ignore_deferred_fields(readonly_db, reference_differential, build):
    # when
    result = await reference_differential(build)
    # then
    assert result.sync_outcome.error is None
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("build", VALUES_OF_DEFERRED)
def test_values_of_deferred_querysets_carry_no_deferred_marker(readonly_db, build):
    # when
    markers = count_deferred_markers(build)
    # then
    assert markers == 0


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            deferred_spec("defer", ("author",), select_related=("author",)), id="defer-traversed-fk"
        ),
        pytest.param(
            deferred_spec("only", ("title",), select_related=("author",)),
            id="only-misses-traversed-fk",
        ),
    ],
)
async def test_reference_deferred_traversed_relation_raises_field_error_on_both_sides(
    readonly_db, reference_differential, spec
):
    # when
    result = await reference_differential(partial(build_queryset, spec))
    # then
    assert not is_valid_deferral(spec)
    assert result.sync_outcome.error == error_name(FieldError())
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("fetch_mode", [FETCH_ONE, FETCH_PEERS], ids=["fetch-one", "fetch-peers"])
@given(spec=valid_deferred_specs())
def test_snapshot_of_generated_deferred_querysets_runs_no_extra_query(
    readonly_db, spec: QuerySetSpec, fetch_mode
):
    # when
    _rows, evaluated, snapshotted = evaluate_and_snapshot_query_counts(
        partial(build_queryset, spec), fetch_mode
    )
    # then
    # an empty window or a contradictory filter compiles to no query at all
    assert (evaluated <= 1, snapshotted) == (True, 0)


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            deferred_spec(
                "only", ("author__name",), select_related=("author", "author__publisher")
            ),
            id="only-misses-nested-publisher",
        ),
        pytest.param(
            deferred_spec("defer", ("author__profile",), select_related=("author__profile",)),
            id="defer-traversed-nested-one-to-one",
        ),
        pytest.param(
            deferred_spec("defer", ("author", "author__name"), select_related=("author",)),
            id="defer-fk-with-its-field-is-valid",
        ),
        pytest.param(
            deferred_spec("defer", ("author",), select_related=("author__profile",)),
            id="defer-implicitly-traversed-parent",
        ),
        pytest.param(
            deferred_spec(
                "only",
                ("author__name", "author__publisher__country"),
                select_related=("author", "author__publisher"),
            ),
            id="only-reaches-every-traversed-path",
        ),
    ],
)
async def test_reference_deferral_outcome_follows_the_validity_rule(
    readonly_db, reference_differential, spec
):
    # when
    result = await reference_differential(partial(build_queryset, spec))
    # then
    assert result.sync_outcome.error == expected_error(spec)
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(
            lambda: Book.objects.defer("metadata").order_by("pk"),
            {"metadata"},
            id="defer-one",
        ),
        pytest.param(
            partial(build_queryset, deferred_spec("defer", ("pages",), order_by=("-pages",))),
            {"pages"},
            id="defer-the-ordering-field",
        ),
        pytest.param(
            lambda: Book.objects.only("title", "price").defer("price").order_by("pk"),
            {"author_id", "pages", "published", "published_at", "metadata", "price"},
            id="only-then-defer",
        ),
    ],
)
def test_deferred_querysets_defer_exactly_the_requested_fields(readonly_db, build, expected):
    # when
    names = deferred_field_names(build)
    # then
    assert names == expected
