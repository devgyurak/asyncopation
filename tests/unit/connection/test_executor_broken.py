"""A statement cancelled inside a transaction breaks the pinned executor (ADR-0001 Decision 6c)."""

import pytest
from django.db.transaction import TransactionManagementError

from asyncopation.connection.executor import pin
from tests.support.executors.ops import CONTROLS_THAT_RAISE_WHEN_BROKEN, STATEMENTS, call, kinds_of
from tests.support.executors.scenarios import (
    broken_pin,
    cancel_in_pin,
    cancel_owner_mid_statement,
    second_pin_after_broken_exit,
)


async def test_cancelled_statement_in_a_transaction_marks_the_executor_broken(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True)
    # then
    assert outcome.broken is True


@pytest.mark.parametrize("op", ["execute", "execute_many", "stream"])
async def test_any_cancelled_statement_in_a_transaction_marks_the_executor_broken(execenv, op):
    # when
    outcome = await cancel_in_pin(execenv, begin=True, op=op)
    # then
    assert outcome.broken is True


async def test_cancelled_commit_marks_the_executor_broken(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True, op="commit")
    # then
    assert outcome.broken is True


async def test_the_cancellation_still_reaches_the_caller(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True)
    # then
    assert type(outcome.cancelled).__name__ == "CancelledError"


async def test_cancelled_statement_without_a_transaction_does_not_break_the_executor(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=False)
    # then
    assert outcome.broken is False


async def test_executor_stays_usable_after_a_cancelled_autocommit_statement(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=False)
    # then
    assert outcome.statement_error is None


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS_THAT_RAISE_WHEN_BROKEN])
async def test_broken_executor_refuses_the_operation(execenv, op):
    # when / then
    async with broken_pin(execenv) as ex:
        with pytest.raises(TransactionManagementError):
            await call(ex, op)


async def test_refused_operation_on_a_broken_executor_never_reaches_the_driver(execenv):
    # given
    async with broken_pin(execenv) as ex:
        calls_before = execenv.default.statement_calls
        with pytest.raises(TransactionManagementError):
            await call(ex, "fetch_all")
        # when
        calls_after = execenv.default.statement_calls
    # then
    assert calls_after == calls_before


async def test_rollback_on_a_broken_executor_is_a_no_op_that_returns(execenv):
    # given
    async with broken_pin(execenv) as ex:
        # when
        result = await ex.rollback()
        kinds = kinds_of(execenv.default)
    # then
    assert result is None
    assert kinds == ["begin", "fetch_all"]


async def test_rollback_on_a_broken_executor_can_be_repeated(execenv):
    # when
    async with broken_pin(execenv) as ex:
        await ex.rollback()
        await ex.rollback()
    # then
    assert "rollback" not in kinds_of(execenv.default)


async def test_leaving_a_broken_pin_does_not_roll_back_through_the_driver(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True)
    # then
    assert outcome.kinds_after_exit == ["begin", "fetch_all"]


async def test_leaving_a_broken_pin_discards_the_connection(execenv):
    # given
    async with broken_pin(execenv):
        pass
    # when
    discarded = execenv.default.discarded
    # then
    assert discarded == 1


async def test_leaving_a_broken_pin_does_not_raise_and_closes_the_executor(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True)
    # then
    assert outcome.closed_after_exit is True


async def test_a_new_pin_after_a_broken_one_is_healthy(execenv):
    # when
    broken, rows = await second_pin_after_broken_exit(execenv)
    # then
    assert (broken, rows) == (False, [(1,)])


async def test_broken_state_belongs_to_one_alias(execenv):
    # given
    async with broken_pin(execenv), pin("other") as other:
        # when
        rows = await other.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


@pytest.mark.parametrize(("begin", "broken"), [(True, True), (False, False)])
async def test_real_cancellation_of_the_owner_breaks_only_inside_a_transaction(
    execenv, begin, broken
):
    # when
    outcome = await cancel_owner_mid_statement(execenv, begin=begin)
    # then
    assert (outcome.cancelled, outcome.broken) == (True, broken)


async def test_real_cancellation_of_the_owner_leaves_nothing_in_flight(execenv):
    # when
    outcome = await cancel_owner_mid_statement(execenv, begin=True)
    # then
    assert outcome.in_flight == 0
