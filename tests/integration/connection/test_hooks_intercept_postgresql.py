"""ADR-0005 interceptors on a real PostgreSQL connection (issue #4): rewritten and annotated SQL
reaches the server, a suppressed query never does, and an interceptor cancelled inside a real
pinned transaction leaves it usable and rollback-able."""

import asyncio

import pytest

from harness.settings import VENDOR
from tests.support.hooks.events import BOTH
from tests.support.hooks.postgres_intercept import (
    ANNOTATION,
    CURRENT_QUERY,
    MISSING_TABLE,
    SUPPLIED,
    annotated_real_fetch,
    cancel_interceptor_in_real_transaction,
    rewritten_real_fetch,
    suppressed_in_real_transaction,
    suppressed_real_fetch,
)

pytestmark = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="the executors run on asyncmy with issue #7b; the interceptor integration tests run on "
    "PostgreSQL only until then",
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
WAITING = pytest.mark.parametrize("after", [False, True], ids=["before-execute", "after-execute"])


@PINNED
async def test_the_server_runs_the_sql_an_interceptor_annotated(spy, pinned):
    # when
    observed = await annotated_real_fetch(pinned=pinned)
    # then
    assert observed.error is None
    assert observed.result == [(ANNOTATION + CURRENT_QUERY,)]


@PINNED
async def test_an_annotated_real_query_is_observed_with_the_original_sql(spy, pinned):
    # when
    observed = await annotated_real_fetch(pinned=pinned)
    # then
    assert observed.names == BOTH
    assert observed.started.sql == CURRENT_QUERY


@PINNED
async def test_the_server_runs_the_statement_and_parameters_an_interceptor_rewrote(spy, pinned):
    # when
    observed = await rewritten_real_fetch(pinned=pinned)
    # then
    assert (observed.result, observed.error) == ([(20,)], None)


@PINNED
async def test_a_suppressed_real_query_returns_the_supplied_rows_without_reaching_the_server(
    spy, pinned
):
    # when
    observed = await suppressed_real_fetch(pinned=pinned)
    # then: the table does not exist, so the server would have raised
    assert (observed.result, observed.error) == (SUPPLIED, None)


@PINNED
async def test_a_suppressed_real_query_is_observed_with_its_supplied_rowcount(spy, pinned):
    # when
    observed = await suppressed_real_fetch(pinned=pinned)
    # then
    assert (observed.finished.sql, observed.finished.rowcount) == (MISSING_TABLE, len(SUPPLIED))


async def test_a_suppressed_query_in_a_real_transaction_leaves_it_usable(spy):
    # when
    outcome = await suppressed_in_real_transaction()
    # then
    assert (outcome.result, outcome.error, outcome.still_in_transaction) == (SUPPLIED, None, True)


@WAITING
async def test_an_interceptor_cancelled_in_a_real_transaction_propagates_the_cancellation(
    spy, after
):
    # when
    outcome = await cancel_interceptor_in_real_transaction(after=after)
    # then
    assert isinstance(outcome.error, asyncio.CancelledError)


@WAITING
async def test_an_interceptor_cancelled_in_a_real_transaction_leaves_it_usable(spy, after):
    # when
    outcome = await cancel_interceptor_in_real_transaction(after=after)
    # then
    assert (outcome.broken, outcome.still_in_transaction) == (False, True)


@WAITING
async def test_rollback_after_an_interceptor_cancelled_in_a_real_transaction_undoes_it(spy, after):
    # when
    outcome = await cancel_interceptor_in_real_transaction(after=after)
    # then
    assert (outcome.rollback_error, outcome.table_after_rollback) == (None, None)
