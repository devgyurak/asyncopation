"""Driver contract (ADR-0001 Decision 6b, ADR-0003): a statement cancelled inside a transaction
discards the connection, and `rollback()` on it is a no-op."""

import pytest
from django.db import InterfaceError

from tests.support.executors.contract import cancel_statement_then_rollback, use_after_discard


async def test_cancelled_statement_in_a_transaction_discards_the_connection(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=True)
    # then
    assert outcome.connection_discarded is True


async def test_discarded_connections_are_counted_on_the_driver(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=True)
    # then
    assert outcome.driver_discarded == 1


async def test_rollback_after_the_discard_is_a_no_op_that_does_not_raise(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=True)
    # then
    assert outcome.rollback_error is None


async def test_neither_the_rollback_nor_the_release_reaches_the_log_after_the_discard(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=True)
    # then
    assert outcome.kinds == ["begin", "fetch_all"]


async def test_cancelled_statement_in_autocommit_keeps_the_connection(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=False)
    # then
    assert (outcome.connection_discarded, outcome.driver_discarded) == (False, 0)


async def test_a_cancelled_statement_surfaces_as_cancellation(driver):
    # when
    outcome = await cancel_statement_then_rollback(driver, begin=True)
    # then
    assert type(outcome.cancelled).__name__ == "CancelledError"


@pytest.mark.parametrize(
    "op",
    ["fetch_all", "execute", "execute_many", "stream", "begin", "commit", "savepoint"],
)
async def test_using_a_discarded_connection_raises_interface_error(driver, op):
    # when
    error = await use_after_discard(driver, op)
    # then
    assert type(error) is InterfaceError
