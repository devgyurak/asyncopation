"""`compile_query`: result kind, single `as_sql` call, column bookkeeping and autocommit."""

import pytest
from django.db.transaction import TransactionManagementError

from asyncopation.engine.compiled import ResultKind
from tests.support.engine.stages import as_sql_calls, compile_case, param_values


def test_compile_query_of_an_unsatisfiable_filter_is_empty(alias):
    # when
    compiled = compile_case("book_empty", alias)
    # then
    assert (compiled.kind, compiled.sql, compiled.params, compiled.col_count) == (
        ResultKind.EMPTY,
        "",
        (),
        0,
    )


@pytest.mark.parametrize("name", ["book_all", "book_empty", "book_ordering_column"])
def test_compile_query_calls_as_sql_exactly_once(alias, name):
    # when
    calls = as_sql_calls(name, alias)
    # then
    assert calls == 1


def test_compile_query_carries_the_sql_and_params_of_the_compiler(alias):
    # when
    compiled = compile_case("book_filtered", alias)
    # then
    assert (compiled.kind, param_values(compiled)) == (ResultKind.MULTI, (5,))
    assert compiled.sql.startswith('SELECT "catalog_book"."id"')


@pytest.mark.parametrize(
    ("name", "col_count", "has_extra"),
    [
        ("book_all", 8, False),
        ("book_extra_select", 9, False),
        ("book_ordering_column", 1, True),
    ],
)
def test_compile_query_records_the_column_bookkeeping(alias, name, col_count, has_extra):
    # when
    compiled = compile_case(name, alias)
    # then
    assert (compiled.col_count, compiled.has_extra_select) == (col_count, has_extra)


def test_compile_query_keeps_the_compiler_that_produced_the_sql(alias):
    # when
    compiled = compile_case("book_all", alias)
    # then
    assert compiled.compiler.col_count == compiled.col_count


def test_compile_query_compiles_select_for_update_when_not_autocommit(alias):
    # when
    compiled = compile_case("book_for_update", alias, autocommit=False)
    # then
    assert compiled.sql.endswith("FOR UPDATE")


def test_compile_query_refuses_select_for_update_in_autocommit(alias):
    # when / then
    with pytest.raises(TransactionManagementError):
        compile_case("book_for_update", alias, autocommit=True)
