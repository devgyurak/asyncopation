"""Driver contract for executors (ADR-0001 Decision 6b, ADR-0003): a statement cancelled while in
a transaction closes the raw connection, and `rollback()` afterwards is a no-op."""

from tests.support.drivers import POSTGRES_ONLY
from tests.support.executors.driver_contract import cancel_then_reuse, cancel_then_rollback

pytestmark = POSTGRES_ONLY


async def test_cancelled_statement_in_a_transaction_closes_the_backend(driver):
    # when
    outcome = await cancel_then_rollback(driver, begin=True)
    # then
    assert outcome.cancelled is True
    assert outcome.backend_ended is True


async def test_rollback_after_a_cancelled_statement_in_a_transaction_is_a_no_op(driver):
    # when
    outcome = await cancel_then_rollback(driver, begin=True)
    # then
    assert outcome.rollback_error is None


async def test_the_pool_replaces_the_connection_after_the_cancel(single_driver):
    # when
    outcome = await cancel_then_rollback(single_driver, begin=True)
    # then
    assert outcome.fresh_pid != outcome.old_pid


async def test_cancelled_statement_outside_a_transaction_keeps_the_backend(driver):
    # when
    outcome = await cancel_then_rollback(driver, begin=False)
    # then
    assert outcome.cancelled is True
    assert outcome.backend_ended is False


async def test_connection_is_idle_and_usable_right_after_a_cancelled_statement(single_driver):
    # when
    outcome = await cancel_then_reuse(single_driver)
    # then
    assert outcome.cancelled is True
    assert outcome.next_rows == [(1,)]
    assert outcome.idle_after is True
    assert outcome.pid_after == outcome.pid_before
