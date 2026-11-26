"""Interceptors on the executors (issue #4, ADR-0005 Decision 4): pass-through, suppression,
rewriting, what is not interceptable, misuse that raises `TypeError` / `ConcurrentQueryError` /
`RuntimeError`, and exceptions that propagate (unlike an observer's)."""

import asyncio

import pytest

from asyncopation import ConcurrentQueryError
from asyncopation.drivers.base import ExecResult
from tests.support.executors.ops import CONTROLS, kinds_of
from tests.support.hooks.events import BOTH
from tests.support.hooks.intercepting import (
    intercepted_call,
    statement_after_the_query,
    task_left_behind,
)
from tests.support.hooks.interceptors import (
    SUPPLIED_ROWCOUNT,
    GatheringTwice,
    Given,
    InAnotherTask,
    LeavingATask,
    ManyFlipping,
    MethodChanging,
    PassThrough,
    RaisingAfter,
    RaisingBefore,
    Retrying,
    Returning,
    Rewriting,
    StoringExecute,
    Suppressing,
    Switching,
    driver_result,
    supplied_result,
    synchronous_interceptor,
)
from tests.support.hooks.scenarios import ARGS

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
OPS = pytest.mark.parametrize("op", ["fetch_all", "execute", "execute_many"])


class InterceptorFailure(Exception):
    """What a failing interceptor raises."""


# --------------------------------------------------------------------------- pass-through
@OPS
@PINNED
async def test_a_pass_through_interceptor_returns_the_driver_result_unchanged(execenv, op, pinned):
    # given
    queued = driver_result(op)
    execenv.default.queue(queued)
    # when
    observed = await intercepted_call(op, PassThrough(), pinned=pinned)
    # then
    assert observed.error is None
    assert observed.result is queued


@pytest.mark.parametrize(
    ("op", "many"), [("fetch_all", False), ("execute", False), ("execute_many", True)]
)
@PINNED
async def test_an_interceptor_is_given_the_statement_many_and_context(execenv, op, many, pinned):
    # given
    interceptor = PassThrough()
    sql, params = ARGS[op]
    # when
    await intercepted_call(op, interceptor, pinned=pinned)
    # then
    assert interceptor.calls == [
        Given(sql, params, many, {"alias": "default", "method": op, "pinned": pinned})
    ]


@OPS
@PINNED
async def test_a_pass_through_interceptor_lets_the_driver_run_the_original_statement(
    execenv, op, pinned
):
    # given
    sql, params = ARGS[op]
    # when
    await intercepted_call(op, PassThrough(), pinned=pinned)
    # then
    assert execenv.default.statements == [(op, sql, params)]


@OPS
@PINNED
async def test_an_intercepted_statement_is_observed_once_as_started_then_finished(
    execenv, op, pinned
):
    # when
    observed = await intercepted_call(op, PassThrough(), pinned=pinned)
    # then
    assert observed.names == BOTH


# --------------------------------------------------------------------------- suppression
@OPS
@PINNED
async def test_a_suppressing_interceptor_returns_the_supplied_result(execenv, op, pinned):
    # given
    supplied = supplied_result(op)
    # when
    observed = await intercepted_call(op, Suppressing(supplied), pinned=pinned)
    # then
    assert observed.error is None
    assert observed.result is supplied


@OPS
@PINNED
async def test_a_suppressed_statement_never_reaches_the_driver(execenv, op, pinned):
    # when
    await intercepted_call(op, Suppressing(supplied_result(op)), pinned=pinned)
    # then
    assert (execenv.default.statements, execenv.default.statement_calls) == ([], 0)


@OPS
@PINNED
async def test_a_suppressed_statement_is_still_observed_with_the_original_sql(execenv, op, pinned):
    # when
    observed = await intercepted_call(op, Suppressing(supplied_result(op)), pinned=pinned)
    # then
    assert observed.names == BOTH
    assert observed.recorder.sqls == [ARGS[op][0]]
    assert observed.finished.sql == ARGS[op][0]


@OPS
@PINNED
async def test_a_suppressed_statement_finishes_with_the_rowcount_of_the_supplied_result(
    execenv, op, pinned
):
    # when
    observed = await intercepted_call(op, Suppressing(supplied_result(op)), pinned=pinned)
    # then
    assert (observed.finished.rowcount, observed.finished.error) == (SUPPLIED_ROWCOUNT[op], None)


# --------------------------------------------------------------------------- rewriting
REWRITES = pytest.mark.parametrize(
    ("op", "sql", "params"),
    [
        pytest.param("fetch_all", "SELECT 'rewritten', %s", (2,), id="fetch_all"),
        pytest.param("execute", "UPDATE t SET x = %s", (9,), id="execute"),
        pytest.param("execute_many", "INSERT INTO u VALUES (%s)", [(3,), (4,), (5,)], id="many"),
    ],
)


@REWRITES
@PINNED
async def test_the_driver_receives_the_statement_an_interceptor_rewrote(
    execenv, op, sql, params, pinned
):
    # when
    await intercepted_call(op, Rewriting(sql, params), pinned=pinned)
    # then
    assert execenv.default.statements == [(op, sql, params)]


@REWRITES
@PINNED
async def test_a_rewritten_statement_is_observed_with_the_original_sql(
    execenv, op, sql, params, pinned
):
    # when
    observed = await intercepted_call(op, Rewriting(sql, params), pinned=pinned)
    # then
    assert observed.started.sql == ARGS[op][0]
    assert observed.finished.sql == ARGS[op][0]


@OPS
@PINNED
async def test_a_rewritten_statement_returns_the_driver_result(execenv, op, pinned):
    # given
    queued = driver_result(op)
    execenv.default.queue(queued)
    # when
    observed = await intercepted_call(op, Rewriting("SELECT 'other'", None), pinned=pinned)
    # then
    assert observed.result is queued


# --------------------------------------------------------------------------- not interceptable
@PINNED
async def test_a_stream_never_calls_an_interceptor(execenv, pinned):
    # given
    interceptor = PassThrough()
    # when
    observed = await intercepted_call("stream", interceptor, pinned=pinned)
    # then
    assert observed.error is None
    assert interceptor.calls == []


@PINNED
async def test_a_stream_under_an_interceptor_is_still_observed(execenv, pinned):
    # when
    observed = await intercepted_call("stream", PassThrough(), pinned=pinned)
    # then
    assert observed.names == BOTH


@pytest.mark.parametrize("op", CONTROLS)
async def test_transaction_control_never_calls_an_interceptor(execenv, op):
    # given
    interceptor = PassThrough()
    # when
    observed = await intercepted_call(op, interceptor, pinned=True)
    # then
    assert observed.error is None
    assert interceptor.calls == []


@pytest.mark.parametrize("op", CONTROLS)
async def test_transaction_control_under_an_interceptor_is_still_observed(execenv, op):
    # when
    observed = await intercepted_call(op, PassThrough(), pinned=True)
    # then
    assert observed.names == BOTH


@pytest.mark.parametrize("op", CONTROLS)
async def test_transaction_control_under_an_interceptor_reaches_the_driver(execenv, op):
    # when
    await intercepted_call(op, Suppressing([]), pinned=True)
    # then: (the release of a pin left in a transaction rolls back after it)
    assert kinds_of(execenv.default)[:1] == [op]


# --------------------------------------------------------------------------- misuse -> TypeError
@OPS
@PINNED
async def test_execute_with_many_not_matching_the_method_raises_type_error(execenv, op, pinned):
    # when
    observed = await intercepted_call(op, ManyFlipping(), pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)
    assert execenv.default.statements == []


@pytest.mark.parametrize("method", ["stream", "begin", "savepoint", "nope", None])
@PINNED
async def test_execute_with_a_non_interceptable_method_raises_type_error(execenv, method, pinned):
    # when
    observed = await intercepted_call("fetch_all", MethodChanging(method), pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)
    assert execenv.default.statements == []


METHOD_CHANGES_OF_RESULT_TYPE = pytest.mark.parametrize(
    ("op", "method"),
    [
        pytest.param("fetch_all", "execute", id="fetch_all-as-execute"),
        pytest.param("execute", "fetch_all", id="execute-as-fetch_all"),
        pytest.param("execute_many", "fetch_all", id="execute_many-as-fetch_all"),
    ],
)


@METHOD_CHANGES_OF_RESULT_TYPE
@PINNED
async def test_a_method_change_to_another_result_type_raises_type_error(
    execenv, op, method, pinned
):
    # when
    observed = await intercepted_call(op, MethodChanging(method), pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)


@METHOD_CHANGES_OF_RESULT_TYPE
@PINNED
async def test_a_method_change_to_another_result_type_never_reaches_the_driver(
    execenv, op, method, pinned
):
    # when
    await intercepted_call(op, MethodChanging(method), pinned=pinned)
    # then
    assert (execenv.default.statements, execenv.default.statement_calls) == ([], 0)


@PINNED
async def test_observers_get_the_type_error_of_a_method_change_to_another_result_type(
    execenv, pinned
):
    # when
    observed = await intercepted_call("fetch_all", MethodChanging("execute"), pinned=pinned)
    # then
    assert observed.names == BOTH
    assert observed.finished.error is observed.error


@PINNED
async def test_an_execute_changed_to_execute_many_runs_execute_many_on_the_driver(execenv, pinned):
    # given
    batch = [(1,), (2,)]
    # when
    await intercepted_call("execute", Switching("execute_many", True, batch), pinned=pinned)
    # then
    assert execenv.default.statements == [("execute_many", ARGS["execute"][0], batch)]


@PINNED
async def test_an_execute_changed_to_execute_many_returns_its_exec_result(execenv, pinned):
    # given
    queued = ExecResult(rowcount=2)
    execenv.default.queue(queued)
    # when
    observed = await intercepted_call(
        "execute", Switching("execute_many", True, [(1,), (2,)]), pinned=pinned
    )
    # then
    assert (observed.result, observed.error) == (queued, None)
    assert observed.result is queued


@OPS
@PINNED
async def test_a_synchronous_interceptor_raises_type_error(execenv, op, pinned):
    # when
    observed = await intercepted_call(op, synchronous_interceptor, pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)
    assert "must be async" in str(observed.error)


@pytest.mark.parametrize(
    ("op", "value"),
    [
        pytest.param("fetch_all", 42, id="fetch_all-int"),
        pytest.param("fetch_all", None, id="fetch_all-none"),
        pytest.param("fetch_all", ((1,),), id="fetch_all-tuple"),
        pytest.param("fetch_all", ExecResult(rowcount=1), id="fetch_all-exec-result"),
        pytest.param("execute", 42, id="execute-int"),
        pytest.param("execute", None, id="execute-none"),
        pytest.param("execute", [(1,)], id="execute-rows"),
        pytest.param("execute_many", None, id="execute_many-none"),
        pytest.param("execute_many", [(1,)], id="execute_many-rows"),
    ],
)
@PINNED
async def test_a_chain_returning_the_wrong_type_raises_type_error(execenv, op, value, pinned):
    # when
    observed = await intercepted_call(op, Returning(value), pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)


@pytest.mark.parametrize(
    "interceptor",
    [
        pytest.param(ManyFlipping(), id="many-mismatch"),
        pytest.param(MethodChanging("stream"), id="non-interceptable-method"),
        pytest.param(MethodChanging("execute"), id="method-change-to-another-result-type"),
        pytest.param(synchronous_interceptor, id="not-awaitable"),
        pytest.param(Returning(42), id="wrong-type"),
    ],
)
@PINNED
async def test_observers_get_the_type_error_of_a_misused_chain_as_the_query_error(
    execenv, interceptor, pinned
):
    # when
    observed = await intercepted_call("fetch_all", interceptor, pinned=pinned)
    # then
    assert isinstance(observed.error, TypeError)
    assert observed.names == BOTH
    assert observed.finished.error is observed.error


# --------------------------------------------------------------------------- misuse of execute
@PINNED
async def test_two_concurrent_executes_from_one_interceptor_raise_concurrent_query_error(
    execenv, pinned
):
    # when
    observed = await intercepted_call("fetch_all", GatheringTwice(), pinned=pinned)
    # then
    assert isinstance(observed.error, ConcurrentQueryError)
    assert execenv.default.statements == []


@PINNED
async def test_an_interceptor_running_execute_in_another_task_gets_concurrent_query_error(
    execenv, pinned
):
    # when
    observed = await intercepted_call("fetch_all", InAnotherTask(), pinned=pinned)
    # then
    assert isinstance(observed.error, ConcurrentQueryError)
    assert execenv.default.statements == []


@PINNED
async def test_an_execute_task_left_behind_by_an_interceptor_fails_once_the_query_is_over(
    execenv, pinned
):
    # given
    supplied = supplied_result("fetch_all")
    # when
    result, error = await task_left_behind(LeavingATask(supplied), pinned=pinned)
    # then
    assert result is supplied
    assert isinstance(error, RuntimeError)  # ConcurrentQueryError is one too
    assert execenv.default.statements == []


@PINNED
async def test_calling_a_stored_execute_after_the_query_raises_runtime_error(execenv, pinned):
    # when
    error = await statement_after_the_query(StoringExecute(), pinned=pinned)
    # then
    assert isinstance(error, RuntimeError)
    assert "after its query was over" in str(error)
    assert kinds_of(execenv.default) == ["fetch_all"]


@PINNED
async def test_an_interceptor_may_call_execute_again_after_it_failed(execenv, pinned):
    # given
    first_error = ValueError("first attempt failed")
    execenv.default.queue(first_error, [(5,)])
    interceptor = Retrying()
    # when
    observed = await intercepted_call("fetch_all", interceptor, pinned=pinned)
    # then
    assert (observed.result, observed.error, interceptor.first_error) == ([(5,)], None, first_error)
    assert kinds_of(execenv.default) == ["fetch_all"] * 2


# --------------------------------------------------------------------------- exceptions propagate
@OPS
@PINNED
async def test_an_interceptor_exception_propagates_to_the_caller_unchanged(execenv, op, pinned):
    # given
    failure = InterceptorFailure("interceptor failed")
    # when
    observed = await intercepted_call(op, RaisingBefore(failure), pinned=pinned)
    # then
    assert observed.error is failure
    assert getattr(failure, "__notes__", []) == []


@OPS
@PINNED
async def test_observers_get_the_interceptor_exception_as_the_query_error(execenv, op, pinned):
    # given
    failure = InterceptorFailure("interceptor failed")
    # when
    observed = await intercepted_call(op, RaisingBefore(failure), pinned=pinned)
    # then
    assert observed.names == BOTH
    assert (observed.finished.error, observed.finished.rowcount) == (failure, None)


@PINNED
async def test_an_interceptor_exception_after_execute_propagates_although_the_driver_ran(
    execenv, pinned
):
    # given
    failure = InterceptorFailure("interceptor failed after the driver")
    interceptor = RaisingAfter(failure)
    # when
    observed = await intercepted_call("fetch_all", interceptor, pinned=pinned)
    # then
    assert observed.error is failure
    assert interceptor.returned == [(1,)]
    assert observed.finished.error is failure


@PINNED
async def test_a_driver_error_passes_through_an_interceptor_unchanged(execenv, pinned):
    # given
    injected = ValueError("driver failure")
    execenv.default.queue(injected)
    # when
    observed = await intercepted_call("fetch_all", PassThrough(), pinned=pinned)
    # then
    assert observed.error is injected
    assert observed.finished.error is injected


@PINNED
async def test_an_interceptor_raising_cancelled_error_propagates_it(execenv, pinned):
    # given
    cancelled = asyncio.CancelledError()
    # when
    observed = await intercepted_call("fetch_all", RaisingBefore(cancelled), pinned=pinned)
    # then
    assert observed.error is cancelled
    assert observed.finished.error is cancelled
