"""`aget`, `afirst`, `acount`, `aexists` over a recording driver: statements, limits, messages."""

import pytest
from django.db.models import Count, Q

from harness.catalog.models import AuthorProfile, Book
from tests.support.engine.catalog import BOOK_ROWS
from tests.support.engine.reads import (
    book_rows,
    distinct_book_rows,
    iterate,
    read,
    read_after_alist,
    read_pinned,
    router_asks,
    without_limit_support,
)
from tests.support.read_cases import (
    alias_filtered,
    annotated_expression,
    distinct_on_title,
    exists_annotated,
    extra_ordered,
    subquery_annotated,
    unordered,
    values_annotated,
    window_filtered,
)

PK_ONE = {"pk": 1}
PROFILE_ROW = (1, 2, "bio", "")


async def test_aget_returns_the_single_row_as_an_instance(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(1), **PK_ONE)
    # then
    assert (reading.error, reading.result.title) == (None, "alpha")


async def test_aget_limits_the_statement_to_21_rows(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(1), **PK_ONE)
    # then
    assert len(reading.sqls) == 1
    assert reading.sqls[0].endswith("LIMIT 21")


async def test_aget_drops_the_ordering(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(1), **PK_ONE)
    # then
    assert "ORDER BY" not in reading.sqls[0]


async def test_aget_with_a_positional_q_filters_like_filter(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(1), Q(pages=5))
    # then
    assert '"catalog_book"."pages" = %s' in reading.sqls[0]


async def test_aget_without_a_row_raises_does_not_exist_with_djangos_message(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, [], **PK_ONE)
    # then
    assert (reading.error_type, reading.message) == (
        "DoesNotExist",
        "Book matching query does not exist.",
    )
    assert isinstance(reading.error, Book.DoesNotExist)


async def test_aget_with_two_rows_raises_multiple_objects_returned_with_the_count(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(2), **PK_ONE)
    # then
    assert (reading.error_type, reading.message) == (
        "MultipleObjectsReturned",
        "get() returned more than one Book -- it returned 2!",
    )
    assert isinstance(reading.error, Book.MultipleObjectsReturned)


async def test_aget_with_21_rows_says_more_than_20(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(21), **PK_ONE)
    # then
    assert reading.message == "get() returned more than one Book -- it returned more than 20!"


async def test_aget_with_20_rows_still_reports_the_exact_count(compile_env):
    # when
    reading = await read(compile_env, "aget", Book.objects.all, book_rows(20), **PK_ONE)
    # then
    assert reading.message == "get() returned more than one Book -- it returned 20!"


async def test_aget_after_a_combinator_with_filters_raises_before_any_statement(compile_env):
    # given
    def build():
        return Book.objects.filter(pk=1).union(Book.objects.filter(pk=2))

    # when
    reading = await read(compile_env, "aget", build, book_rows(1), **PK_ONE)
    # then
    assert (reading.error_type, reading.message, reading.sqls) == (
        "NotSupportedError",
        "Calling QuerySet.get(...) with filters after union() is not supported.",
        [],
    )


async def test_aget_inside_a_pin_compiles_select_for_update_with_the_limit(compile_env):
    # when
    reading = await read_pinned(
        compile_env, "aget", Book.objects.select_for_update, book_rows(1), **PK_ONE
    )
    # then
    assert reading.error is None
    assert reading.sqls[0].endswith("LIMIT 21 FOR UPDATE")


async def test_aget_of_select_for_update_follows_the_wrapper_limit_feature(
    compile_env, monkeypatch
):
    # given
    without_limit_support(monkeypatch)
    # when
    reading = await read_pinned(
        compile_env, "aget", Book.objects.select_for_update, book_rows(2), **PK_ONE
    )
    # then
    assert "LIMIT" not in reading.sqls[0]
    assert reading.message == "get() returned more than one Book -- it returned 2!"


async def test_aget_of_select_for_update_outside_a_pin_raises_before_any_statement(compile_env):
    # when
    reading = await read(
        compile_env, "aget", Book.objects.select_for_update, book_rows(1), **PK_ONE
    )
    # then
    assert (reading.error_type, reading.sqls) == ("TransactionManagementError", [])


async def test_afirst_of_an_unordered_queryset_orders_by_pk_and_limits_to_one(compile_env):
    # when
    reading = await read(compile_env, "afirst", AuthorProfile.objects.all, [PROFILE_ROW])
    # then
    assert reading.sqls[0].endswith('ORDER BY "catalog_authorprofile"."id" ASC LIMIT 1')


async def test_afirst_of_an_ordered_queryset_keeps_its_ordering(compile_env):
    # given
    def build():
        return Book.objects.order_by("-pages")

    # when
    reading = await read(compile_env, "afirst", build, book_rows(1))
    # then
    assert reading.sqls[0].endswith('ORDER BY "catalog_book"."pages" DESC LIMIT 1')


async def test_afirst_returns_the_first_instance(compile_env):
    # when
    reading = await read(compile_env, "afirst", Book.objects.all, book_rows(1))
    # then
    assert reading.result.title == "alpha"


async def test_afirst_without_a_row_returns_none(compile_env):
    # when
    reading = await read(compile_env, "afirst", Book.objects.all, [])
    # then
    assert (reading.result, reading.error) == (None, None)


async def test_afirst_of_an_unordered_aggregating_queryset_raises_type_error(compile_env):
    # given
    def build():
        return AuthorProfile.objects.values("website").annotate(n=Count("pk"))

    # when
    reading = await read(compile_env, "afirst", build, [])
    # then
    assert (reading.error_type, reading.sqls) == ("TypeError", [])


async def test_acount_issues_one_count_statement_and_returns_the_int(compile_env):
    # when
    reading = await read(compile_env, "acount", Book.objects.all, [(42,)])
    # then
    assert reading.result == 42
    assert len(reading.sqls) == 1
    assert reading.sqls[0].startswith("SELECT COUNT(*)")


async def test_acount_of_none_issues_no_statement(compile_env):
    # when
    reading = await read(compile_env, "acount", Book.objects.none, [(42,)])
    # then
    assert (reading.result, reading.sqls) == (0, [])


async def test_aexists_issues_one_limit_one_statement(compile_env):
    # when
    reading = await read(compile_env, "aexists", Book.objects.all, [(1,)])
    # then
    assert reading.result is True
    assert len(reading.sqls) == 1
    assert reading.sqls[0].endswith("LIMIT 1")


async def test_aexists_without_a_row_is_false(compile_env):
    # when
    reading = await read(compile_env, "aexists", Book.objects.all, [])
    # then
    assert reading.result is False


async def test_aexists_of_none_issues_no_statement(compile_env):
    # when
    reading = await read(compile_env, "aexists", Book.objects.none, [(1,)])
    # then
    assert (reading.result, reading.sqls) == (False, [])


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        pytest.param("acount", 3, id="acount"),
        pytest.param("aexists", True, id="aexists"),
    ],
)
async def test_a_filled_result_cache_answers_the_cached_value(compile_env, op, expected):
    # when
    reading = await read_after_alist(compile_env, op, Book.objects.all, book_rows(3))
    # then
    assert reading.result == expected


@pytest.mark.parametrize("op", ["acount", "aexists"])
async def test_a_filled_result_cache_answers_without_a_statement(compile_env, op):
    # when
    reading = await read_after_alist(compile_env, op, Book.objects.all, book_rows(3))
    # then
    assert len(reading.sqls) == 1  # the alist() only


async def test_afirst_of_a_filled_result_cache_answers_without_a_statement(compile_env):
    # when
    reading = await read_after_alist(compile_env, "afirst", Book.objects.all, book_rows(3))
    # then
    assert reading.result.title == "alpha"


async def test_afirst_of_a_filled_result_cache_issues_no_second_statement(compile_env):
    # when
    reading = await read_after_alist(compile_env, "afirst", Book.objects.all, book_rows(3))
    # then
    assert len(reading.sqls) == 1


async def test_aexists_of_an_empty_result_cache_is_false_without_a_statement(compile_env):
    # when
    reading = await read_after_alist(compile_env, "aexists", Book.objects.all, [])
    # then
    assert reading.result is False


async def test_aexists_of_an_empty_result_cache_issues_no_second_statement(compile_env):
    # when
    reading = await read_after_alist(compile_env, "aexists", Book.objects.all, [])
    # then
    assert len(reading.sqls) == 1


async def test_acount_of_an_empty_result_cache_is_zero_without_a_statement(compile_env):
    # when
    reading = await read_after_alist(compile_env, "acount", Book.objects.all, [])
    # then
    assert (reading.result, len(reading.sqls)) == (0, 1)  # the alist() only


# -- aget keeps the ordering DISTINCT ON needs ---------------------------------------------------


async def test_aget_of_a_distinct_on_queryset_keeps_its_ordering(compile_env):
    # when
    reading = await read(compile_env, "aget", distinct_on_title, book_rows(1), **PK_ONE)
    # then
    assert reading.error is None
    assert 'ORDER BY "catalog_book"."title" ASC' in reading.sqls[0]


async def test_aget_of_a_distinct_on_queryset_keeps_the_distinct_on_clause(compile_env):
    # when
    reading = await read(compile_env, "aget", distinct_on_title, book_rows(1), **PK_ONE)
    # then
    assert 'SELECT DISTINCT ON ("catalog_book"."title")' in reading.sqls[0]


async def test_afirst_of_a_queryset_with_cleared_ordering_adds_no_order_by(compile_env):
    # when
    reading = await read(compile_env, "afirst", unordered, book_rows(1))
    # then
    assert "ORDER BY" not in reading.sqls[0]


async def test_afirst_of_a_queryset_with_cleared_ordering_still_limits_to_one_row(compile_env):
    # when
    reading = await read(compile_env, "afirst", unordered, book_rows(1))
    # then
    assert reading.sqls[0].endswith("LIMIT 1")


# -- what cannot match issues no statement -------------------------------------------------------


@pytest.mark.parametrize("op", ["acount", "aexists", "afirst"])
@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: Book.objects.filter(pk__in=[]), id="empty-in"),
        pytest.param(Book.objects.none, id="none"),
        pytest.param(lambda: Book.objects.order_by("pk")[:0], id="empty-window"),
    ],
)
async def test_a_read_that_cannot_match_issues_no_statement(compile_env, op, build):
    # when
    reading = await read(compile_env, op, build, [(1,)])
    # then
    assert reading.sqls == []


async def test_afirst_of_none_returns_none(compile_env):
    # when
    reading = await read(compile_env, "afirst", Book.objects.none, book_rows(1))
    # then
    assert (reading.result, reading.error) == (None, None)


# -- the shape of the count statement ------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(values_annotated, id="values-grouped"),
        pytest.param(alias_filtered, id="filter-on-an-aliased-aggregate"),
        pytest.param(window_filtered, id="filter-on-a-window"),
    ],
)
async def test_acount_counts_over_a_subquery_when_the_rows_are_grouped_or_filtered(
    compile_env, build
):
    # when
    reading = await read(compile_env, "acount", build, [(5,)])
    # then
    assert reading.result == 5
    assert reading.sqls[0].startswith("SELECT COUNT(*) FROM (SELECT")


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(annotated_expression, id="annotated-expression"),
        pytest.param(exists_annotated, id="exists-annotation"),
        pytest.param(subquery_annotated, id="subquery-annotation"),
        pytest.param(extra_ordered, id="extra-select-ordered"),
    ],
)
async def test_acount_drops_annotations_extra_selects_and_ordering_nothing_refers_to(
    compile_env, build
):
    # when
    reading = await read(compile_env, "acount", build, [(5,)])
    # then
    assert reading.sqls == ['SELECT COUNT(*) AS "__count" FROM "catalog_book"']


# -- async for -----------------------------------------------------------------------------------


async def test_async_for_yields_the_rows_in_order(compile_env):
    # when
    iterated = await iterate(compile_env, Book.objects.all, distinct_book_rows(2))
    # then
    assert iterated.titles == ["alpha", "beta"]


async def test_async_for_fills_the_result_cache(compile_env):
    # when
    iterated = await iterate(compile_env, Book.objects.all, book_rows(2))
    # then
    assert iterated.cached


async def test_async_for_over_a_cached_wrapper_issues_no_further_statement(compile_env):
    # when
    iterated = await iterate(compile_env, Book.objects.all, book_rows(2), after_alist=True)
    # then
    assert len(iterated.sqls) == 1  # the alist() only


async def test_async_for_over_a_cached_wrapper_yields_the_cached_rows(compile_env):
    # when
    iterated = await iterate(compile_env, Book.objects.all, distinct_book_rows(2), after_alist=True)
    # then
    assert iterated.titles == ["alpha", "beta"]


# -- the database router is asked once per evaluation ----------------------------------------------


@pytest.mark.parametrize(
    ("op", "rows", "kwargs"),
    [
        pytest.param("aget", [BOOK_ROWS[0]], {"pk": 1}, id="aget"),
        pytest.param("afirst", [BOOK_ROWS[0]], {}, id="afirst"),
        pytest.param("acount", [(3,)], {}, id="acount"),
        pytest.param("aexists", [(1,)], {}, id="aexists"),
    ],
)
async def test_a_read_asks_the_database_router_once(compile_env, op, rows, kwargs):
    # when
    asks = await router_asks(compile_env, op, Book.objects.all, rows, **kwargs)
    # then
    assert asks == 1


async def test_aget_of_a_locking_queryset_asks_the_database_router_once(compile_env):
    # when
    asks = await router_asks(
        compile_env, "aget", Book.objects.select_for_update, [BOOK_ROWS[0]], pinned=True, pk=1
    )
    # then
    assert asks == 1
