"""`execute_multi`: no I/O for EMPTY, trimming of extra columns, rows otherwise untouched."""

from asyncopation.engine.compiled import ResultKind
from tests.support.engine.stages import execute_case

ORDERED_ROWS = [("alpha", 120), ("beta", 0)]
PLAIN_ROWS = [(1, "a"), (2, "b")]


async def test_execute_multi_of_an_empty_query_does_no_io(alias):
    # when
    executed = await execute_case("book_empty", alias, PLAIN_ROWS)
    # then
    assert (executed.compiled.kind, executed.result, executed.fetched) == (ResultKind.EMPTY, [], [])


async def test_execute_multi_trims_rows_to_col_count_with_extra_select(alias):
    # when
    executed = await execute_case("book_ordering_column", alias, ORDERED_ROWS)
    # then
    assert executed.result == [("alpha",), ("beta",)]


async def test_execute_multi_returns_the_executor_rows_untouched_otherwise(alias):
    # when
    executed = await execute_case("book_all", alias, PLAIN_ROWS)
    # then
    assert executed.result is PLAIN_ROWS


async def test_execute_multi_fetches_once_with_the_compiled_sql_and_params(alias):
    # when
    executed = await execute_case("book_filtered", alias, [])
    # then
    assert executed.fetched == [(executed.compiled.sql, executed.compiled.params)]
