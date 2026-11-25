"""A statement cancelled inside a transaction breaks the pinned executor (ADR-0001 Decision 6c)."""

import asyncio

import pytest
from django.db.transaction import TransactionManagementError

from tests.support.executors.broken import (
    other_alias_beside_a_broken_pin,
    roll_back_a_broken_pin,
    use_a_broken_pin,
)
from tests.support.executors.ops import CONTROLS_THAT_RAISE_WHEN_BROKEN, STATEMENTS
from tests.support.executors.scenarios import (
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
    assert type(outcome.cancelled) is asyncio.CancelledError


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
    # when
    outcome = await use_a_broken_pin(execenv, op)
    # then
    assert type(outcome.error) is TransactionManagementError


@pytest.mark.parametrize("op", STATEMENTS)
async def test_refused_statement_on_a_broken_executor_never_reaches_the_driver(execenv, op):
    # when
    outcome = await use_a_broken_pin(execenv, op)
    # then
    assert outcome.calls_after == outcome.calls_before


async def test_rollback_on_a_broken_executor_is_a_no_op_that_returns(execenv):
    # when
    outcome = await roll_back_a_broken_pin(execenv)
    # then
    assert outcome.errors == [None]
    assert outcome.kinds == ["begin", "fetch_all"]


async def test_rollback_on_a_broken_executor_can_be_repeated(execenv):
    # when
    outcome = await roll_back_a_broken_pin(execenv, times=2)
    # then
    assert outcome.errors == [None, None]
    assert "rollback" not in outcome.kinds


async def test_no_rollback_is_sent_for_a_connection_the_driver_discarded(execenv):
    # when
    outcome = await cancel_in_pin(execenv, begin=True)
    # then
    assert outcome.kinds_after_exit == ["begin", "fetch_all"]


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
    # when
    rows = await other_alias_beside_a_broken_pin(execenv)
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


async def test_a_lost_connection_in_a_transaction_makes_the_next_statement_raise(execenv):
    # when
    outcome = await use_a_broken_pin(execenv, "fetch_all", cause="lost_connection")
    # then
    assert type(outcome.error) is TransactionManagementError


async def test_a_statement_refused_on_a_pin_broken_by_a_lost_connection_stays_off_the_driver(
    execenv,
):
    # when
    outcome = await use_a_broken_pin(execenv, "fetch_all", cause="lost_connection")
    # then
    assert outcome.calls_after == outcome.calls_before


async def test_rollback_on_a_pin_broken_by_a_lost_connection_is_a_no_op(execenv):
    # when
    outcome = await roll_back_a_broken_pin(execenv, cause="lost_connection")
    # then
    assert outcome.errors == [None]
