"""`evaluate_list` over a recording driver: open first, one fetch, cache, early refusals."""

from tests.support.engine.evaluate import (
    evaluate,
    evaluate_in_pin,
    evaluate_outside_pin_error,
    evaluate_routed,
    evaluate_unsupported,
    expected_statement,
)


async def test_evaluate_list_opens_the_alias_before_compiling(compile_env):
    # when
    evaluation = await evaluate(compile_env)
    # then
    assert (evaluation.opened_before, evaluation.wrapper_asked_while_open) == (False, True)


async def test_evaluate_list_issues_one_fetch_all_with_the_compiled_sql(compile_env):
    # when
    evaluation = await evaluate(compile_env, "book_filtered")
    # then
    assert evaluation.statement_tuples == [expected_statement("book_filtered")]


async def test_evaluate_list_returns_the_materialized_instances(compile_env):
    # when
    evaluation = await evaluate(compile_env)
    # then
    assert evaluation.titles == ["alpha", "beta", "gamma"]


async def test_evaluate_list_fills_the_result_cache(compile_env):
    # when
    evaluation = await evaluate(compile_env)
    # then
    assert evaluation.cache == evaluation.result
    assert evaluation.cache_size == 3


async def test_evaluate_list_returns_a_copy_of_the_cache_list(compile_env):
    # when
    evaluation = await evaluate(compile_env)
    # then
    assert evaluation.result_is_the_cache_list is False


async def test_evaluate_list_a_second_call_returns_the_cache_without_io(compile_env):
    # when
    evaluation = await evaluate(compile_env, times=2)
    # then
    assert (len(evaluation.statements), evaluation.borrows, len(evaluation.result)) == (1, 1, 3)


async def test_evaluate_list_of_an_empty_query_fetches_nothing_and_caches_empty(compile_env):
    # when
    evaluation = await evaluate(compile_env, "book_empty")
    # then
    assert (evaluation.result, evaluation.cache, evaluation.statements) == ([], [], [])


async def test_evaluate_list_refuses_an_unsupported_iterable_before_any_io(compile_env):
    # when
    outcome = await evaluate_unsupported(compile_env)
    # then
    assert outcome == ("NotImplementedError", False, [], 0)


async def test_evaluate_list_inside_a_pin_compiles_in_transaction_mode(compile_env):
    # when
    result = await evaluate_in_pin(compile_env, "book_for_update")
    # then
    assert result == []
    assert compile_env.default.statements[-1].sql.endswith("FOR UPDATE")


async def test_evaluate_list_outside_a_pin_compiles_in_autocommit_mode(compile_env):
    # when
    error = await evaluate_outside_pin_error(compile_env, "book_for_update")
    # then
    assert error == "TransactionManagementError"


async def test_evaluate_list_asks_the_router_once_and_keeps_that_alias(compile_env):
    # when
    routed = await evaluate_routed(compile_env, ["default", "other", "other", "other"])
    # then
    assert routed.router_reads == 1
    assert routed.instance_aliases == {"default"}
