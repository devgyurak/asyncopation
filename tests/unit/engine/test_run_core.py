"""`run_core`: the async runner of the cores over a recording driver and the real compile stage."""

import pytest

from asyncopation._django.connections import ScopeContextError
from asyncopation.connection import registry
from asyncopation.engine.cores import aggregation_core, count_core, exists_core
from asyncopation.engine.runner import run_core
from tests.support.engine.converters import (
    aggregation_conversion_error,
    aggregation_conversion_scope,
    alist_conversion_scope,
)
from tests.support.engine.cores import (
    annotated_book_query,
    book_query,
    catching_core,
    compile_catching_core,
    distinct_book_query,
    empty_book_query,
    multi_core,
    outside_transaction_query,
    plain_core,
    scope_reading_handler_core,
    sliced_book_query,
)
from tests.support.engine.evaluate import ALIAS
from tests.support.engine.runs import (
    BrokenRepr,
    Interrupt,
    autocommit_seen_by_core,
    exceptions_seen_by_a_cancelled_core,
    run_cancelled_in_flight,
    run_cancelled_with_failing_cleanup,
    run_cancelled_with_interrupting_cleanup,
    run_cancelled_with_scope_enter_error,
    run_cancelled_with_scope_error,
    run_expecting_error,
    run_failed_with_failing_cleanup,
    run_finalizing,
    run_finalizing_with_error,
    run_stubborn_cancelled_in_flight,
    scope_open_while_a_cancelled_core_closes,
    scope_seen_by_statements,
)
from tests.support.read_cases import (
    alias_filtered,
    exists_annotated,
    values_annotated,
    window_filtered,
)


async def test_run_core_counts_with_one_count_star_statement(compile_env):
    # given
    compile_env.default.queue([(5,)])
    # when
    result = await run_core(count_core(book_query()), ALIAS)
    # then
    assert result == 5
    assert compile_env.default.kinds() == ["fetch_all"]
    assert compile_env.default.statements[0].sql.startswith('SELECT COUNT(*) AS "__count"')


async def test_run_core_opens_the_alias_before_compiling(compile_env):
    # given
    compile_env.default.queue([(5,)])
    # when
    await run_core(count_core(book_query()), ALIAS)
    # then
    assert set(compile_env.opened_when_wrapper_asked) == {True}


async def test_run_core_counts_an_empty_set_as_zero_without_any_fetch(compile_env):
    # when
    result = await run_core(count_core(empty_book_query()), ALIAS)
    # then
    assert result == 0
    assert compile_env.default.statements == []


async def test_run_core_of_a_core_that_never_yields_returns_its_value(compile_env):
    # when
    result = await run_core(aggregation_core(book_query(), {}), ALIAS)
    # then
    assert result == {}
    assert compile_env.default.statements == []


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(sliced_book_query, id="sliced"),
        pytest.param(distinct_book_query, id="distinct"),
        pytest.param(annotated_book_query, id="annotated"),
        pytest.param(lambda: values_annotated().query, id="values-grouped"),
        pytest.param(lambda: alias_filtered().query, id="filter-on-an-aliased-aggregate"),
        pytest.param(lambda: window_filtered().query, id="filter-on-a-window"),
    ],
)
async def test_run_core_counts_a_sliced_distinct_or_annotated_query_over_a_subquery(
    compile_env, build
):
    # given
    compile_env.default.queue([(5,)])
    # when
    result = await run_core(count_core(build()), ALIAS)
    # then
    assert result == 5
    assert "FROM (SELECT" in compile_env.default.statements[0].sql


async def test_run_core_counts_a_plain_query_without_a_subquery(compile_env):
    # given
    compile_env.default.queue([(5,)])
    # when
    await run_core(count_core(book_query()), ALIAS)
    # then
    assert "(SELECT" not in compile_env.default.statements[0].sql


async def test_run_core_asks_exists_with_a_limit_one_statement(compile_env):
    # given
    compile_env.default.queue([(1,)])
    # when
    result = await run_core(exists_core(book_query()), ALIAS)
    # then
    assert result is True
    assert compile_env.default.statements[0].sql.endswith("LIMIT 1")


async def test_run_core_answers_exists_false_for_no_row(compile_env):
    # given
    compile_env.default.queue([])
    # when
    result = await run_core(exists_core(book_query()), ALIAS)
    # then
    assert result is False


async def test_run_core_throws_an_execute_error_into_the_core(compile_env):
    # given
    compile_env.default.queue(ValueError("boom"))
    # when
    result = await run_core(catching_core(book_query()), ALIAS)
    # then
    assert result == "caught boom"


async def test_run_core_throws_a_compile_error_into_the_core(compile_env):
    # when
    result = await run_core(compile_catching_core(outside_transaction_query()), ALIAS)
    # then
    assert result == "caught TransactionManagementError"
    assert compile_env.default.statements == []


async def test_run_core_propagates_an_error_the_core_does_not_handle(compile_env):
    # given
    compile_env.default.queue(RuntimeError("driver failed"))
    # when
    error = await run_expecting_error(plain_core(book_query()), ALIAS)
    # then
    assert (type(error), str(error)) == (RuntimeError, "driver failed")


async def test_run_core_leaves_no_statement_in_flight_after_an_error(compile_env):
    # given
    compile_env.default.queue(RuntimeError("driver failed"))
    # when
    await run_expecting_error(plain_core(book_query()), ALIAS)
    # then
    assert registry.in_flight(ALIAS) == 0


async def test_run_core_counts_a_query_with_a_masked_annotation_without_a_subquery(compile_env):
    # given
    compile_env.default.queue([(5,)])
    # when
    await run_core(count_core(exists_annotated().query), ALIAS)
    # then: the annotation is masked, so no subquery and no EXISTS reach the statement
    assert (
        compile_env.default.statements[0].sql == 'SELECT COUNT(*) AS "__count" FROM "catalog_book"'
    )


async def test_run_core_closes_a_core_that_ran_to_its_end(compile_env):
    # when
    log = await run_finalizing(compile_env, book_query())
    # then
    assert log == ["closed"]


async def test_run_core_closes_a_core_whose_statement_failed(compile_env):
    # when
    log = await run_finalizing_with_error(compile_env, book_query())
    # then
    assert log == ["closed"]


async def test_run_core_cancelled_in_flight_propagates_cancelled_error(compile_env):
    # when
    run = await run_cancelled_in_flight(compile_env, book_query())
    # then
    assert run.cancelled


async def test_run_core_cancelled_in_flight_closes_the_core(compile_env):
    # when
    run = await run_cancelled_in_flight(compile_env, book_query())
    # then
    assert run.log == ["closed"]


async def test_run_core_closes_a_cancelled_core_instead_of_throwing_the_cancellation(compile_env):
    # when
    log = await exceptions_seen_by_a_cancelled_core(compile_env, book_query())
    # then: `close()` raises GeneratorExit in the core; the CancelledError never reaches it
    assert log == ["GeneratorExit"]


async def test_run_core_keeps_the_cancellation_when_the_core_ignores_the_close(compile_env):
    # when
    run = await run_stubborn_cancelled_in_flight(compile_env, book_query())
    # then: close() raised RuntimeError in the runner, which must not replace the CancelledError
    assert run.cancelled


@pytest.mark.parametrize("error", [ValueError("cleanup failed"), RuntimeError("cleanup failed")])
async def test_run_core_keeps_the_cancellation_when_the_core_cleanup_raises(compile_env, error):
    # when
    run = await run_cancelled_with_failing_cleanup(compile_env, book_query(), error)
    # then: neither a ValueError nor a RuntimeError from a `finally` replaces the CancelledError
    assert run.cancelled


@pytest.mark.parametrize("error", [ValueError("cleanup failed"), RuntimeError("cleanup failed")])
async def test_run_core_attaches_the_cleanup_error_to_the_cancellation(compile_env, error):
    # when
    run = await run_cancelled_with_failing_cleanup(compile_env, book_query(), error)
    # then
    assert run.notes == (f"closing the core also raised {error!r}",)


async def test_run_core_attaches_the_error_of_a_core_that_ignores_the_close(compile_env):
    # when
    run = await run_stubborn_cancelled_in_flight(compile_env, book_query())
    # then
    assert len(run.notes) == 1
    assert run.notes[0].startswith("closing the core also raised RuntimeError(")


async def test_run_core_does_not_swallow_a_scope_error_when_the_close_exits_the_scope(
    compile_env, monkeypatch
):
    # when
    error = await run_cancelled_with_scope_error(compile_env, book_query(), monkeypatch)
    # then: a ScopeContextError is a RuntimeError, but only core.close() is guarded
    assert type(error) is ScopeContextError


async def test_run_core_propagates_a_base_exception_from_the_close(compile_env):
    # when
    error = await run_cancelled_with_interrupting_cleanup(compile_env, book_query())
    # then: only an Exception is attached as a note; an Interrupt (a BaseException) propagates
    assert type(error) is Interrupt


async def test_run_core_attaches_the_cleanup_error_to_the_exception_of_a_failed_run(compile_env):
    # when
    error = await run_failed_with_failing_cleanup(
        compile_env, book_query(), ValueError("cleanup failed")
    )
    # then
    assert error is not None
    assert error.__notes__ == [f"closing the core also raised {ValueError('cleanup failed')!r}"]


async def test_run_core_names_a_cleanup_error_whose_repr_fails_by_its_type(compile_env):
    # when
    run = await run_cancelled_with_failing_cleanup(compile_env, book_query(), BrokenRepr())
    # then: the note is built defensively, so the CancelledError is kept and the type is named
    assert (run.cancelled, run.notes) == (
        True,
        ("closing the core also raised BrokenRepr (its repr failed)",),
    )


async def test_run_core_does_not_swallow_a_scope_error_when_the_close_enters_the_scope(
    compile_env, monkeypatch
):
    # when
    error = await run_cancelled_with_scope_enter_error(compile_env, book_query(), monkeypatch)
    # then: entering the scope for close() is outside the guarded call
    assert type(error) is ScopeContextError


async def test_run_core_closes_a_core_that_ignores_the_close_once(compile_env):
    # when
    run = await run_stubborn_cancelled_in_flight(compile_env, book_query())
    # then
    assert run.log == ["ignored GeneratorExit"]


async def test_run_core_cancelled_in_flight_leaves_no_statement_in_flight(compile_env):
    # when
    run = await run_cancelled_in_flight(compile_env, book_query())
    # then
    assert run.in_flight_after == 0


async def test_run_core_keeps_the_compile_scope_out_of_every_await(compile_env):
    # when
    seen = await scope_seen_by_statements(compile_env, book_query())
    # then
    assert seen == [False]


async def test_run_core_compiles_in_autocommit_mode_without_a_pin(compile_env):
    # when
    before, after = await autocommit_seen_by_core(book_query(), pinned=False)
    # then
    assert (before, after) == (True, True)


async def test_run_core_compiles_inside_a_transaction_in_a_pin(compile_env):
    # when
    before, after = await autocommit_seen_by_core(book_query(), pinned=True)
    # then
    assert (before, after) == (False, False)


async def test_run_core_throws_an_execute_error_into_the_core_inside_the_compile_scope(compile_env):
    # given
    compile_env.default.queue(ValueError("boom"))
    # when
    result = await run_core(scope_reading_handler_core(book_query(), ALIAS), ALIAS)
    # then
    assert result is True


async def test_run_core_closes_a_cancelled_core_inside_the_compile_scope(compile_env):
    # when
    log = await scope_open_while_a_cancelled_core_closes(compile_env, book_query())
    # then
    assert log == [True]


async def test_run_core_converts_an_aggregate_outside_the_compile_scope(compile_env):
    # when
    seen = await aggregation_conversion_scope(compile_env)
    # then: user `from_db_value` code sees no compile-only wrapper
    assert seen == [False]


async def test_evaluate_list_converts_rows_outside_the_compile_scope(compile_env):
    # when
    seen = await alist_conversion_scope(compile_env)
    # then
    assert seen == [False, False]


async def test_run_core_raises_the_error_of_a_conversion_that_the_core_does_not_handle(
    compile_env,
):
    # when
    error = await aggregation_conversion_error(compile_env)
    # then
    assert (type(error), str(error)) == (ValueError, "conversion failed")


async def test_run_core_answers_a_multi_statement_with_every_row(compile_env):
    # given
    compile_env.default.queue([(1,), (2,), (3,)])
    # when
    rows = await run_core(multi_core(book_query()), ALIAS)
    # then
    assert rows == [(1,), (2,), (3,)]
