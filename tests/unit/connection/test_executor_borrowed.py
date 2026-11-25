"""Borrowed executor: one connection per statement and a balanced in-flight counter (ADR-0001)."""

import asyncio

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.connection.executor import BorrowedExecutor
from tests.support.executors.ops import STATEMENTS, call, kinds_of
from tests.support.executors.scenarios import (
    ALIAS,
    borrow_from_unknown_alias,
    cancel_borrowed_mid_statement,
    concurrent_borrowed,
    failing_borrowed_statement,
    trace_borrowed_in_flight,
)


def test_borrowed_executor_is_not_pinned_and_knows_its_alias():
    # when
    executor = BorrowedExecutor(ALIAS)
    # then
    assert (executor.pinned, executor.alias) == (False, ALIAS)


@pytest.mark.parametrize("op", STATEMENTS)
async def test_borrowed_executor_runs_every_statement_kind(execenv, op):
    # when
    await call(BorrowedExecutor(ALIAS), op)
    # then
    assert kinds_of(execenv.default) == [op]


async def test_borrowed_fetch_all_returns_the_driver_rows(execenv):
    # given
    execenv.default.queue([(7, "a"), (8, "b")])
    # when
    rows = await BorrowedExecutor(ALIAS).fetch_all("SELECT 1", None)
    # then
    assert rows == [(7, "a"), (8, "b")]


async def test_borrowed_statement_counts_as_in_flight_only_while_it_runs(execenv):
    # when
    trace = await trace_borrowed_in_flight(execenv)
    # then
    assert (trace.before, trace.inside_statement, trace.after) == (0, 1, 0)


async def test_the_statement_is_counted_before_the_connection_is_borrowed(execenv):
    # when
    trace = await trace_borrowed_in_flight(execenv)
    # then
    assert trace.at_borrow == [1]


@pytest.mark.parametrize("op", STATEMENTS)
async def test_in_flight_is_balanced_after_a_failing_statement(execenv, op):
    # when
    outcome = await failing_borrowed_statement(execenv, op, ValueError("boom"))
    # then
    assert isinstance(outcome.error, ValueError)
    assert outcome.in_flight == 0


@pytest.mark.parametrize("op", STATEMENTS)
async def test_in_flight_is_balanced_after_a_cancelled_statement(execenv, op):
    # when
    outcome = await failing_borrowed_statement(execenv, op, asyncio.CancelledError())
    # then
    assert type(outcome.error).__name__ == "CancelledError"
    assert outcome.in_flight == 0


async def test_the_connection_is_released_after_a_failing_statement(execenv):
    # when
    outcome = await failing_borrowed_statement(execenv, "fetch_all", ValueError("boom"))
    # then
    assert outcome.released == [True]


async def test_in_flight_is_balanced_when_the_task_is_really_cancelled(execenv):
    # when
    cancelled, during, after = await cancel_borrowed_mid_statement(execenv)
    # then
    assert (cancelled, during, after) == (True, 1, 0)


async def test_in_flight_is_balanced_when_the_alias_cannot_be_opened(execenv):
    # when
    outcome = await borrow_from_unknown_alias()
    # then
    assert type(outcome.error) is ImproperlyConfigured
    assert outcome.in_flight == 0


async def test_gathered_borrowed_statements_run_concurrently_on_separate_connections(execenv):
    # when
    outcome = await concurrent_borrowed(execenv, 3)
    # then
    assert (outcome.peak, outcome.borrows) == (3, 3)


async def test_every_gathered_connection_is_released_and_nothing_stays_in_flight(execenv):
    # when
    outcome = await concurrent_borrowed(execenv, 3)
    # then
    assert (outcome.after, outcome.released) == (0, [True, True, True])
