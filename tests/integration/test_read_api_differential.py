"""`aget`, `afirst`, `acount`, `aexists` and `async for` match the sync ORM: values, types, order
and exceptions (type and message), on every backend (issue #6, PR #67)."""

from dataclasses import replace
from functools import partial

import pytest
from django.db.models import Count, Q
from hypothesis import given
from hypothesis import strategies as st

from harness.catalog.models import Author, AuthorProfile, Book, BookEdition, Publisher, Tag
from tests.support.backends import POSTGRESQL_ONLY, XFAIL_UNTIL_ASYNCMY
from tests.support.manager_models import attach_pass_through, pass_through_calls, restore_aobjects
from tests.support.read_api import (
    aobjects_statements_of_two_alists,
    cancel_acount_in_flight,
    cancel_locking_aget_in_a_pin,
    cancel_read_in_flight,
    compare,
    compare_aobjects,
    compare_chained_aobjects,
    compare_in_transaction,
    compare_manager_method,
    gathered_aget_and_iteration,
    gathered_on_one_bound_object,
    gathered_reads,
    iterate_twice,
    iteration_sync_connection_attempts,
    read_with_filled_cache,
    statements_of,
    sync_aget_and_list,
    sync_connection_attempts,
    sync_list_and_count,
    sync_trio,
    sync_value,
)
from tests.support.read_cases import (
    alias_filtered,
    annotated_expression,
    distinct_on_title,
    exists_annotated,
    extra_ordered,
    extra_values,
    extra_values_list_ordered,
    locked_sliced,
    subquery_annotated,
    subquery_filtered,
    unordered,
    values_annotated,
    window_filtered,
)
from tests.support.strategies import (
    QuerySetSpec,
    annotated_spec,
    build_queryset,
    combined_spec,
    deferred_spec,
    get_queryset_specs,
    read_queryset_specs,
)

pytestmark = XFAIL_UNTIL_ASYNCMY


def _union():
    return Book.objects.filter(pk__lt=10).union(Book.objects.filter(pk__gt=5))


def _sliced():
    return Book.objects.order_by("pk")[2:7]


def _distinct_sliced():
    return Book.objects.filter(tags__isnull=False).distinct().order_by("pk")[:3]


def _annotated():
    return Book.objects.annotate(n=Count("tags"))


def _values_distinct():
    return Book.objects.values("author").distinct()


# -- get ------------------------------------------------------------------------------------------


async def test_aget_by_pk_matches_sync(async_env, book_pk):
    # when
    pair = await compare(Book.objects.all, "aget", pk=book_pk)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_by_a_unique_filter_matches_sync(async_env):
    # when
    pair = await compare(Tag.objects.all, "aget", name="tag-0")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_with_a_q_object_matches_sync(async_env, book_pk):
    # when
    pair = await compare(Book.objects.all, "aget", Q(pk=book_pk) & Q(published__in=[True, False]))
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_on_a_prefiltered_queryset_matches_sync(async_env, book_pk):
    # when
    pair = await compare(partial(Book.objects.filter, pk=book_pk), "aget")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_without_a_match_raises_the_same_does_not_exist(async_env):
    # when
    pair = await compare(Book.objects.all, "aget", title="missing")
    # then
    assert pair.sync.error_type == "harness.catalog.models.Book.DoesNotExist"
    assert pair.async_ == pair.sync


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Tag.objects.all, id="few-rows"),
        pytest.param(Book.objects.all, id="more-than-the-limit"),
    ],
)
async def test_aget_with_several_matches_raises_the_same_multiple_objects_returned(
    async_env, build
):
    # when
    pair = await compare(build, "aget")
    # then
    assert ".MultipleObjectsReturned" in str(pair.sync.error_type)
    assert pair.async_ == pair.sync


async def test_aget_with_several_matches_reports_the_row_count_in_the_message(async_env):
    # when
    pair = await compare(Tag.objects.all, "aget")
    # then
    assert pair.async_.error == (
        "harness.catalog.models.Tag.MultipleObjectsReturned",
        "get() returned more than one Tag -- it returned 10!",
    )


async def test_aget_of_a_combinator_without_filters_matches_sync(async_env, book_pk):
    # given
    def build():
        return Book.objects.filter(pk=book_pk).union(Book.objects.filter(pk=book_pk))

    # when
    pair = await compare(build, "aget")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_by_a_composite_pk_tuple_matches_sync(async_env, edition_keys):
    # when
    pair = await compare(BookEdition.objects.all, "aget", pk=edition_keys[0])
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_on_a_values_queryset_matches_sync(async_env, book_pk):
    # given
    def build():
        return Book.objects.values("title", "price", "published_at")

    # when
    pair = await compare(build, "aget", pk=book_pk)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_with_select_related_matches_sync(async_env, book_pk):
    # given
    def build():
        return Book.objects.select_related("author__publisher")

    # when
    pair = await compare(build, "aget", pk=book_pk)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_with_select_for_update_in_a_transaction_matches_sync(async_env, book_pk):
    # when
    pair = await compare_in_transaction(Book.objects.select_for_update, "aget", pk=book_pk)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_with_select_for_update_outside_a_transaction_raises_like_sync(
    async_env, book_pk
):
    # when
    pair = await compare(Book.objects.select_for_update, "aget", pk=book_pk)
    # then
    assert pair.sync.error_type == "django.db.transaction.TransactionManagementError"
    assert pair.async_ == pair.sync


@POSTGRESQL_ONLY
async def test_aget_of_a_distinct_on_queryset_matches_sync(async_env, book_pk):
    # when
    pair = await compare(distinct_on_title, "aget", pk=book_pk)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aget_without_arguments_on_a_sliced_queryset_matches_sync(async_env):
    # when
    pair = await compare(lambda: Book.objects.order_by("pk")[:1], "aget")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


# -- first ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Book.objects.order_by("-pages", "pk"), id="ordered"),
        pytest.param(Book.objects.all, id="default-meta-ordering"),
        pytest.param(AuthorProfile.objects.all, id="unordered-falls-back-to-pk"),
        pytest.param(lambda: Book.objects.filter(published=True), id="filtered"),
        pytest.param(_sliced, id="sliced"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Book.objects.values("title", "pages"), id="values"),
        pytest.param(lambda: Book.objects.values_list("title", flat=True), id="values-list-flat"),
        pytest.param(_union, id="union"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
        pytest.param(extra_ordered, id="extra-select-ordered"),
        pytest.param(extra_values, id="extra-select-values"),
        pytest.param(extra_values_list_ordered, id="extra-select-values-list-ordered"),
        pytest.param(unordered, id="cleared-ordering"),
        pytest.param(distinct_on_title, id="distinct-on", marks=POSTGRESQL_ONLY),
    ],
)
async def test_afirst_matches_sync(async_env, build):
    # when
    pair = await compare(build, "afirst")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="empty-in"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(partial(Book.objects.filter, title="missing"), id="no-match"),
    ],
)
async def test_afirst_of_no_rows_is_none_like_sync(async_env, build):
    # when
    pair = await compare(build, "afirst")
    # then
    assert pair.async_.value == ("single", ("value", ("NoneType", None)))
    assert pair.async_ == pair.sync


# -- count ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(partial(Book.objects.filter, published=True), id="filter"),
        pytest.param(_sliced, id="slice"),
        pytest.param(lambda: Book.objects.order_by("pk")[:0], id="empty-window"),
        pytest.param(lambda: Book.objects.filter(tags__isnull=False).distinct(), id="distinct"),
        pytest.param(_annotated, id="annotate"),
        pytest.param(lambda: _annotated().filter(n__gt=1), id="filter-on-annotation"),
        pytest.param(_values_distinct, id="values-distinct"),
        pytest.param(_union, id="union"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="empty-in"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Author.objects.filter(books__pages__gt=100).distinct(), id="join"),
        pytest.param(extra_ordered, id="extra-select-ordered"),
        pytest.param(extra_values, id="extra-select-values"),
        pytest.param(values_annotated, id="values-grouped"),
        pytest.param(alias_filtered, id="filter-on-an-aliased-aggregate"),
        pytest.param(annotated_expression, id="annotated-expression"),
        pytest.param(window_filtered, id="filter-on-a-window"),
        pytest.param(exists_annotated, id="exists-annotation"),
        pytest.param(subquery_annotated, id="subquery-annotation"),
        pytest.param(subquery_filtered, id="subquery-filter"),
    ],
)
async def test_acount_matches_sync(async_env, build):
    # when
    pair = await compare(build, "acount")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_acount_counts_the_seeded_books(async_env):
    # when
    pair = await compare(Book.objects.all, "acount")
    # then
    assert pair.async_.value == ("single", ("value", ("int", 100)))


# -- exists ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="all"),
        pytest.param(partial(Book.objects.filter, title="missing"), id="filter-none"),
        pytest.param(_sliced, id="sliced"),
        pytest.param(lambda: Book.objects.order_by("pk")[1000:], id="sliced-past-the-end"),
        pytest.param(_distinct_sliced, id="distinct-sliced"),
        pytest.param(_union, id="union"),
        pytest.param(_annotated, id="annotate"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(BookEdition.objects.all, id="composite-pk"),
        pytest.param(extra_ordered, id="extra-select-ordered"),
        pytest.param(values_annotated, id="values-grouped"),
        pytest.param(alias_filtered, id="filter-on-an-aliased-aggregate"),
        pytest.param(window_filtered, id="filter-on-a-window"),
        pytest.param(exists_annotated, id="exists-annotation"),
    ],
)
async def test_aexists_matches_sync(async_env, build):
    # when
    pair = await compare(build, "aexists")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


# -- inside and outside a pinned connection ------------------------------------------------------


@pytest.mark.parametrize("op", ["acount", "aexists"])
async def test_a_locking_read_inside_a_pin_matches_sync_in_a_transaction(async_env, op):
    # when
    pair = await compare_in_transaction(locked_sliced, op)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_aexists_of_a_locking_queryset_outside_a_pin_raises_like_sync(async_env):
    # when
    pair = await compare(locked_sliced, "aexists")
    # then
    assert pair.sync.error_type == "django.db.transaction.TransactionManagementError"
    assert pair.async_ == pair.sync


async def test_acount_of_a_locking_queryset_outside_a_pin_matches_sync(async_env):
    # when
    pair = await compare(locked_sliced, "acount")
    # then
    assert pair.sync.error_type is None
    assert pair.async_ == pair.sync


async def test_acount_of_a_locking_queryset_without_a_slice_matches_sync(async_env):
    # when
    pair = await compare(Book.objects.select_for_update, "acount")
    # then
    assert pair.sync.error_type is None
    assert pair.async_ == pair.sync


# -- no sync connection is ever touched ----------------------------------------------------------


@pytest.mark.parametrize("op", ["acount", "aexists", "afirst"])
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_sliced, id="slice"),
        pytest.param(_distinct_sliced, id="distinct"),
        pytest.param(_annotated, id="annotate"),
        pytest.param(_union, id="union"),
    ],
)
async def test_a_read_never_touches_a_sync_connection(async_env, build, op):
    # when
    attempts = await sync_connection_attempts(build, op)
    # then
    assert (attempts.sync_connects, attempts.statements >= 1) == (0, True)


def _distinct():
    return Book.objects.filter(tags__isnull=False).distinct()


@pytest.mark.parametrize(
    ("build", "kwargs"),
    [
        pytest.param(Book.objects.all, {"pk__gte": 0}, id="plain"),
        pytest.param(_distinct, {"pk__gte": 0}, id="distinct"),
        pytest.param(_annotated, {"pk__gte": 0}, id="annotate"),
        pytest.param(_union, {}, id="union"),
    ],
)
async def test_aget_never_touches_a_sync_connection(async_env, build, kwargs):
    # when: several rows match, so `MultipleObjectsReturned` ends the read after its statement
    attempts = await sync_connection_attempts(build, "aget", **kwargs)
    # then
    assert (attempts.sync_connects, attempts.statements >= 1) == (0, True)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_sliced, id="slice"),
        pytest.param(_distinct_sliced, id="distinct"),
        pytest.param(_annotated, id="annotate"),
        pytest.param(_union, id="union"),
    ],
)
async def test_async_for_never_touches_a_sync_connection(async_env, build):
    # when
    attempts = await iteration_sync_connection_attempts(build)
    # then
    assert (attempts.sync_connects, attempts.statements >= 1) == (0, True)


# -- aobjects -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "op", "kwargs"),
    [
        pytest.param(Tag, "aget", {"name": "tag-0"}, id="aget"),
        pytest.param(Book, "afirst", {}, id="afirst"),
        pytest.param(Book, "acount", {}, id="acount"),
        pytest.param(Book, "aexists", {}, id="aexists"),
        pytest.param(Book, "alist", {}, id="alist"),
        pytest.param(Author, "acount", {}, id="acount-author"),
        pytest.param(BookEdition, "afirst", {}, id="afirst-composite-pk"),
    ],
)
async def test_aobjects_reads_match_the_sync_manager(async_env, model, op, kwargs):
    # when
    pair = await compare_aobjects(model, op, **kwargs)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


@pytest.mark.parametrize("op", ["alist", "afirst", "acount", "aexists"])
async def test_a_filtered_aobjects_read_matches_the_sync_manager(async_env, op):
    # when
    pair = await compare_chained_aobjects(Book, op, published=True)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


# -- aobjects through the default manager (ADR-0009) ----------------------------------------------


@pytest.fixture
def pass_through_publisher():
    """`PassThroughAsyncObjects.attach(Publisher)` (an `aget` override calling `super().aget()`),
    the original declared object restored afterwards."""
    original = attach_pass_through(Publisher)
    yield Publisher
    restore_aobjects(Publisher, original)


@pytest.mark.parametrize("country", ["US", "KR", "JP", "FR"])
@pytest.mark.parametrize("op", ["alist", "acount", "afirst"])
async def test_a_manager_only_chaining_method_reads_like_the_sync_manager(async_env, op, country):
    # when
    pair = await compare_manager_method(Publisher, "from_country", op, country)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


@pytest.mark.parametrize("op", ["alist", "afirst"])
async def test_an_all_override_reads_like_the_sync_manager_order_included(async_env, op):
    # when
    pair = await compare_manager_method(Publisher, "all", op)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


async def test_an_aget_override_through_super_reads_like_the_sync_manager(
    async_env, pass_through_publisher
):
    # when
    pair = await compare_aobjects(pass_through_publisher, "aget", name="publisher-0")
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync
    assert pass_through_calls(pass_through_publisher) == ["aget"]


async def test_two_alist_calls_on_one_bound_aobjects_run_two_statements(async_env):
    # when
    statements = await aobjects_statements_of_two_alists(Book)
    # then
    assert statements == 2


async def test_gathered_reads_on_one_bound_aobjects_match_the_sync_manager(async_env):
    # given
    expected = await sync_list_and_count(Book)
    # when
    gathered = await gathered_on_one_bound_object(Book)
    # then
    assert gathered.readings == expected
    assert gathered.statements == 2


# -- what cannot match issues no statement --------------------------------------------------------


@pytest.mark.parametrize("op", ["acount", "aexists", "afirst"])
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="pk-in-empty"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(lambda: Book.objects.order_by("pk")[:0], id="empty-window"),
    ],
)
async def test_a_read_that_cannot_match_issues_no_statement(async_env, build, op):
    # when
    statements = await statements_of(build, op)
    # then
    assert statements == []


# -- result cache ---------------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["acount", "aexists", "afirst"])
async def test_a_filled_result_cache_answers_with_the_sync_value(async_env, op):
    # when
    cached = await read_with_filled_cache(Book.objects.all, op)
    expected = await sync_value(Book.objects.all, op)
    # then
    assert cached.value == expected


@pytest.mark.parametrize("op", ["acount", "aexists", "afirst"])
async def test_a_filled_result_cache_answers_without_a_statement(async_env, op):
    # when
    cached = await read_with_filled_cache(Book.objects.all, op)
    # then
    assert cached.statements == 0


_NO_ROWS = [
    pytest.param(Book.objects.none, id="none"),
    pytest.param(partial(Book.objects.filter, title="missing"), id="no-match"),
]


@pytest.mark.parametrize("build", _NO_ROWS)
async def test_acount_of_an_empty_result_cache_is_zero_like_sync(async_env, build):
    # when
    cached = await read_with_filled_cache(build, "acount")
    expected = await sync_value(build, "acount")
    # then
    assert cached.value == expected


@pytest.mark.parametrize("build", _NO_ROWS)
async def test_acount_of_an_empty_result_cache_is_zero_without_a_statement(async_env, build):
    # when
    cached = await read_with_filled_cache(build, "acount")
    # then
    assert cached.statements == 0


# -- async for ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(Book.objects.all, id="books"),
        pytest.param(lambda: Book.objects.select_related("author"), id="select-related"),
        pytest.param(lambda: Book.objects.values("title"), id="values"),
        pytest.param(partial(Book.objects.filter, pk__in=[]), id="empty"),
    ],
)
async def test_async_for_yields_what_the_sync_list_holds(async_env, build):
    # when
    iterated = await iterate_twice(build)
    expected = (await compare(build, "alist")).sync.value
    # then
    assert iterated.first == expected


async def test_async_for_fills_the_result_cache(async_env):
    # when
    iterated = await iterate_twice(Book.objects.all)
    # then
    assert iterated.cached


async def test_a_second_async_for_issues_no_statement(async_env):
    # when
    iterated = await iterate_twice(Book.objects.all)
    # then
    assert iterated.second_statements == 0


async def test_a_second_async_for_yields_the_same_rows(async_env):
    # when
    iterated = await iterate_twice(Book.objects.all)
    # then
    assert iterated.second == iterated.first


# -- concurrency ----------------------------------------------------------------------------------


@POSTGRESQL_ONLY
async def test_gathered_reads_run_concurrently(async_env):
    # when
    gathered = await gathered_reads(Book.objects.all)
    # then
    assert gathered.peak_in_flight >= 3


@POSTGRESQL_ONLY
async def test_gathered_reads_return_the_sync_values(async_env):
    # when
    gathered = await gathered_reads(Book.objects.all)
    expected = await sync_trio(Book.objects.all)
    # then
    assert gathered.results == expected


@POSTGRESQL_ONLY
async def test_gathered_aget_and_async_for_return_the_sync_values(async_env, book_pk):
    # when
    gathered = await gathered_aget_and_iteration(Book.objects.all, book_pk)
    expected = await sync_aget_and_list(Book.objects.all, book_pk)
    # then
    assert gathered == expected


@POSTGRESQL_ONLY
async def test_gathered_reads_release_every_connection(async_env):
    # when
    gathered = await gathered_reads(Book.objects.all)
    # then
    assert gathered.in_use_after == 0


@POSTGRESQL_ONLY
async def test_a_cancelled_acount_propagates_the_cancellation(async_env):
    # when
    cancelled = await cancel_acount_in_flight(Book.objects.all)
    # then
    assert cancelled.cancelled


@POSTGRESQL_ONLY
@pytest.mark.parametrize("op", ["afirst", "aexists", "aget"])
async def test_a_cancelled_read_leaves_nothing_in_flight_and_every_connection_released(
    async_env, op
):
    # when
    cancelled = await cancel_read_in_flight(Book.objects.all, op)
    # then
    assert (cancelled.cancelled, cancelled.in_flight_after, cancelled.in_use_after) == (True, 0, 0)


@POSTGRESQL_ONLY
async def test_a_cancelled_locking_aget_inside_a_pin_releases_its_connection(async_env, book_pk):
    # when
    cancelled = await cancel_locking_aget_in_a_pin(partial(Book.objects.filter, pk=book_pk))
    # then
    assert (cancelled.cancelled, cancelled.in_flight_after, cancelled.in_use_after) == (True, 0, 0)


@POSTGRESQL_ONLY
async def test_a_cancelled_acount_leaves_nothing_in_flight_and_every_connection_released(async_env):
    # when
    cancelled = await cancel_acount_in_flight(Book.objects.all)
    # then
    assert (cancelled.in_flight_after, cancelled.in_use_after) == (0, 0)


@POSTGRESQL_ONLY
@pytest.mark.parametrize("operation", ["afirst", "acount"])
@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(
            combined_spec(
                deferred_spec("only", ("title",), order_by=("-pages", "-title")), "union"
            ),
            id="only-union-descending",
        ),
        pytest.param(
            combined_spec(
                deferred_spec("defer", ("pages",), order_by=("pages", "-title")), "intersection"
            ),
            id="defer-intersection",
        ),
        pytest.param(
            combined_spec(
                deferred_spec(
                    "only", ("title",), annotations=("n_tags",), order_by=("-n_tags", "-title")
                ),
                "union",
            ),
            id="only-and-annotation-union",
        ),
        pytest.param(
            combined_spec(
                deferred_spec(
                    "defer", ("price",), annotations=("double_pages",), order_by=("-double_pages",)
                ),
                "difference",
            ),
            id="defer-and-annotation-difference",
        ),
        pytest.param(
            combined_spec(
                replace(annotated_spec((), ("-title",)), projection=("flat", ("title",))),
                "union",
            ),
            id="flat-projection-without-pk-union",
        ),
        pytest.param(
            combined_spec(
                replace(
                    annotated_spec((), ("-pages", "title")),
                    projection=("values", ("title", "pages")),
                ),
                "intersection",
            ),
            id="values-projection-without-pk-intersection",
        ),
    ],
)
async def test_a_combined_queryset_of_a_fixed_shape_matches_sync(async_env, spec, operation):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, operation)
    # then
    assert pair.sync.error is None
    assert pair.async_ == pair.sync


# -- random QuerySets -----------------------------------------------------------------------------


@given(spec=read_queryset_specs())
async def test_acount_of_random_querysets_matches_sync(async_env, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, "acount")
    # then
    assert pair.async_ == pair.sync


@given(spec=read_queryset_specs())
async def test_afirst_of_random_querysets_matches_sync(async_env, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, "afirst")
    # then
    assert pair.async_ == pair.sync


@given(spec=read_queryset_specs())
async def test_aexists_of_random_querysets_matches_sync(async_env, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, "aexists")
    # then
    assert pair.async_ == pair.sync


@given(spec=get_queryset_specs())
async def test_aget_by_pk_of_random_querysets_matches_sync(async_env, book_pk, spec: QuerySetSpec):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, "aget", pk=book_pk)
    # then
    assert pair.async_ == pair.sync


@given(spec=get_queryset_specs(), pages=st.integers(0, 1300))
async def test_aget_by_a_non_unique_filter_of_random_querysets_matches_sync(
    async_env, spec: QuerySetSpec, pages: int
):
    # given
    build = partial(build_queryset, spec)
    # when
    pair = await compare(build, "aget", pages__gte=pages)
    # then
    assert pair.async_ == pair.sync
