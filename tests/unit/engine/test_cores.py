"""The sans-I/O cores (`count_core`, `aggregation_core`, `exists_core`) driven without a runner."""

from decimal import Decimal

import pytest
from django.db.models import F

from asyncopation._django import aggregation
from asyncopation.engine.compiled import ResultKind
from asyncopation.engine.cores import aggregation_core, count_core, exists_core
from tests.support.engine.cores import (
    book_query,
    count_exprs,
    drive,
    empty_book_query,
    plus_one_exprs,
    selected_after,
    sum_exprs,
    two_exprs,
    values_annotated_query,
    values_distinct_query,
    values_distinct_sliced_query,
    values_sliced_query,
)


def test_count_core_yields_compile_execute_convert_in_order(alias):
    # when
    trace = drive(count_core(book_query()), alias, row=(3,))
    # then
    assert trace.request_names == ["Compile", "Execute", "Convert"]


def test_count_core_compiles_a_single_row_statement(alias):
    # when
    trace = drive(count_core(book_query()), alias, row=(3,))
    # then
    assert trace.compile_kinds == [ResultKind.SINGLE]


def test_count_core_executes_what_it_compiled(alias):
    # when
    trace = drive(count_core(book_query()), alias, row=(3,))
    # then
    assert trace.executed == trace.compiled


def test_count_core_returns_the_counted_int(alias):
    # when
    trace = drive(count_core(book_query()), alias, row=(7,))
    # then
    assert trace.result == 7
    assert type(trace.result) is int


def test_count_core_compiles_count_star(alias):
    # when
    trace = drive(count_core(book_query()), alias, row=(7,))
    # then
    assert trace.sql.startswith('SELECT COUNT(*) AS "__count" FROM "catalog_book"')


def test_count_core_leaves_the_query_it_was_given_untouched(alias):
    # given
    query = book_query()
    before = str(query)
    # when
    drive(count_core(query), alias, row=(7,))
    # then
    assert str(query) == before


def test_count_core_of_an_empty_set_returns_zero_without_sql(alias):
    # when
    trace = drive(count_core(empty_book_query()), alias)
    # then
    assert (trace.result, trace.compiled[0].kind) == (0, ResultKind.EMPTY)


def test_aggregation_core_of_no_expressions_returns_an_empty_dict_without_yielding(alias):
    # when
    trace = drive(aggregation_core(book_query(), {}), alias)
    # then
    assert trace.result == {}
    assert trace.requests == []


def test_aggregation_core_returns_the_row_keyed_by_alias_in_order(alias):
    # when
    trace = drive(aggregation_core(book_query(), two_exprs()), alias, row=(4, Decimal("1.50")))
    # then
    assert list(trace.result.items()) == [("n", 4), ("total", Decimal("1.50"))]


def test_aggregation_core_yields_compile_execute_convert_for_the_whole_dict(alias):
    # when
    trace = drive(aggregation_core(book_query(), two_exprs()), alias, row=(4, Decimal("1.50")))
    # then
    assert trace.request_names == ["Compile", "Execute", "Convert"]


def test_aggregation_core_asks_to_convert_the_row_it_executed(alias):
    # when
    trace = drive(aggregation_core(book_query(), two_exprs()), alias, row=(4, Decimal("1.50")))
    # then
    convert = trace.requests[2]
    assert (convert.row, convert.compiled) == ((4, Decimal("1.50")), trace.compiled[0])


def test_aggregation_core_compiles_the_aggregates_into_one_select(alias):
    # when
    trace = drive(aggregation_core(book_query(), sum_exprs()), alias, row=(Decimal("2"),))
    # then
    assert trace.sql.startswith('SELECT SUM("catalog_book"."price") AS "total"')


@pytest.mark.parametrize(
    ("exprs", "expected"),
    [
        pytest.param(count_exprs, {"n": 0}, id="count-is-zero"),
        pytest.param(sum_exprs, {"total": None}, id="sum-is-none"),
        pytest.param(two_exprs, {"n": 0, "total": None}, id="both"),
    ],
)
def test_aggregation_core_of_an_empty_set_returns_the_empty_values(alias, exprs, expected):
    # when
    trace = drive(aggregation_core(empty_book_query(), exprs()), alias)
    # then
    assert trace.result == expected


def test_aggregation_core_of_an_empty_set_still_runs_when_an_expression_has_no_empty_value(alias):
    # when: `Sum() + Value(1)` has no `empty_result_set_value` (NotImplemented), so no eliding
    trace = drive(aggregation_core(empty_book_query(), plus_one_exprs()), alias, row=(None,))
    # then
    assert trace.compiled[0].kind is ResultKind.SINGLE
    assert trace.sql.endswith("WHERE 0 = 1")


def test_aggregation_core_of_an_empty_set_returns_the_row_of_the_statement_it_ran(alias):
    # when
    trace = drive(aggregation_core(empty_book_query(), plus_one_exprs()), alias, row=(None,))
    # then
    assert trace.result == {"total": None}


def test_aggregation_core_of_a_row_less_result_returns_the_empty_values(alias):
    # when: the statement ran but returned no row (None)
    trace = drive(aggregation_core(book_query(), sum_exprs()), alias, row=None)
    # then
    assert trace.result == {"total": None}


def test_aggregation_core_rejects_a_non_aggregate_expression(alias):
    # when / then
    with pytest.raises(TypeError, match="is not an aggregate expression"):
        drive(aggregation_core(book_query(), {"x": F("pages")}), alias)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param((1,), True, id="a-row"),
        pytest.param((0,), True, id="a-falsy-value-in-a-row"),
        pytest.param(None, False, id="no-row"),
    ],
)
def test_exists_core_returns_whether_a_row_came_back(alias, row, expected):
    # when
    trace = drive(exists_core(book_query()), alias, row=row)
    # then
    assert trace.result is expected


def test_exists_core_yields_compile_then_execute_in_order(alias):
    # when
    trace = drive(exists_core(book_query()), alias, row=(1,))
    # then
    assert trace.request_names == ["Compile", "Execute"]
    assert trace.compile_kinds == [ResultKind.SINGLE]


def test_exists_core_compiles_a_limit_one_statement(alias):
    # when
    trace = drive(exists_core(book_query()), alias, row=(1,))
    # then
    assert trace.sql.endswith("LIMIT 1")


def test_exists_core_of_an_empty_set_is_false_without_sql(alias):
    # when
    trace = drive(exists_core(empty_book_query()), alias)
    # then
    assert (trace.result, trace.compiled[0].kind) == (False, ResultKind.EMPTY)


@pytest.mark.parametrize("core", [count_core, exists_core])
@pytest.mark.parametrize(
    "build",
    [
        values_distinct_query,
        values_distinct_sliced_query,
        values_sliced_query,
        values_annotated_query,
    ],
)
def test_a_core_leaves_the_selected_dict_of_the_query_it_was_given_untouched(alias, core, build):
    """A guard against a change in Django, not a test of the copy: with Django 6.1 the cores leave
    the dict untouched with or without `clone_query`'s copy (the dedicated `clone_query` tests
    below fail without it); it starts to matter when Django mutates `selected` in place."""
    # when
    before, after = selected_after(core, build(), alias)
    # then
    assert after == before


def test_count_query_gives_the_clone_its_own_selected_dict():
    # given
    query = values_distinct_sliced_query()
    # when
    clone, _ = aggregation.count_query(query)
    # then
    assert clone.selected is not query.selected


def test_count_query_keeps_the_selected_entries_of_the_query():
    # given
    query = values_distinct_sliced_query()
    # when
    clone, _ = aggregation.count_query(query)
    # then
    assert clone.selected == query.selected


def test_exists_query_gives_the_clone_its_own_selected_dict():
    # given
    query = values_distinct_sliced_query()
    # when
    exists = aggregation.exists_query(query)
    # then
    assert exists.selected is not query.selected
