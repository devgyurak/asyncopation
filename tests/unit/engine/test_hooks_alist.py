"""ADR-0005 Validation (issue #4): an observer sees every statement of an `alist()` with the SQL
the driver received."""

import pytest

from tests.support.engine.evaluate import expected_statement
from tests.support.hooks.alist import observed_alist
from tests.support.hooks.events import BOTH

CASES = pytest.mark.parametrize(
    "name",
    ["book_all", "book_filtered", "book_select_related_author", "values_list", "book_annotated"],
)


@CASES
async def test_every_alist_statement_is_observed_with_the_sql_the_driver_received(
    compile_env, name
):
    # when
    observed = await observed_alist(compile_env, name)
    # then
    assert observed.recorder.statements == observed.driver


@CASES
async def test_every_alist_statement_finishes_with_the_sql_the_driver_received(compile_env, name):
    # when
    observed = await observed_alist(compile_env, name)
    # then
    assert observed.recorder.finished_statements == observed.driver


@CASES
async def test_an_alist_statement_is_observed_as_the_compiled_query(compile_env, name):
    # when
    observed = await observed_alist(compile_env, name)
    # then
    assert observed.recorder.statements == [expected_statement(name)[1:]]


@CASES
async def test_an_alist_statement_is_observed_once_as_started_then_finished(compile_env, name):
    # when
    observed = await observed_alist(compile_env, name)
    # then
    assert observed.recorder.names == BOTH
    assert observed.recorder.only_finished.rowcount == observed.rows


async def test_an_alist_that_cannot_match_is_not_observed(compile_env):
    # when
    observed = await observed_alist(compile_env, "book_empty")
    # then
    assert observed.recorder.events == []
