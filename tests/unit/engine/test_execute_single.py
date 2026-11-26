"""`execute_single`: the first row cut to `col_count`, `None` for no row, no I/O for EMPTY."""

from tests.support.engine.single import execute_single_case

ORDERED_ROWS = [("alpha", 120), ("beta", 0)]
PLAIN_ROWS = [(1, "a"), (2, "b")]


async def test_execute_single_returns_only_the_first_row(alias):
    # when
    executed = await execute_single_case("book_all", alias, PLAIN_ROWS)
    # then
    assert executed.result == (1, "a")


async def test_execute_single_trims_the_row_to_col_count(alias):
    # when
    executed = await execute_single_case("book_ordering_column", alias, ORDERED_ROWS)
    # then
    assert executed.result == ("alpha",)


async def test_execute_single_returns_none_when_there_is_no_row(alias):
    # when
    executed = await execute_single_case("book_all", alias, [])
    # then
    assert executed.result is None
    assert len(executed.fetched) == 1


async def test_execute_single_of_an_empty_query_does_no_io(alias):
    # when
    executed = await execute_single_case("book_empty", alias, PLAIN_ROWS)
    # then
    assert (executed.result, executed.fetched) == (None, [])


async def test_execute_single_fetches_once_with_the_compiled_sql_and_params(alias):
    # when
    executed = await execute_single_case("book_filtered", alias, [])
    # then
    assert executed.fetched == [(executed.compiled.sql, executed.compiled.params)]
