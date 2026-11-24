"""Cancellation safety: transactions, open streams, cursor teardown and cleanup failures."""

import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.drivers import (
    POSTGRES_ONLY,
    cancel_after_own_backend_killed,
    cancel_again_during_recovery,
    cancel_commit,
    cancel_in_raw_begin,
    cancel_open_stream,
    cancel_stream_close,
    cancel_stream_fetch,
    cancel_write_in_transaction,
    install_slow_commit,
    rollback_after_timeout_discard,
)

pytestmark = POSTGRES_ONLY

TRANSACTIONAL = pytest.mark.parametrize(
    "transactional", [False, True], ids=["autocommit-withhold", "transaction"]
)


async def test_cancelled_write_in_transaction_leaves_the_row_absent(driver, scratch):
    # when
    outcome = await cancel_write_in_transaction(driver, scratch)
    # then
    assert outcome.cancelled is True
    assert outcome.rows_left == 0


async def test_cancelled_statement_in_transaction_discards_the_connection(driver, scratch):
    # when
    outcome = await cancel_write_in_transaction(driver, scratch)
    # then
    assert outcome.old_backend_ended is True


async def test_cancelled_statement_in_transaction_replaces_the_connection(single_driver, scratch):
    # when
    outcome = await cancel_write_in_transaction(single_driver, scratch)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.fresh_pid != outcome.old_pid


@TRANSACTIONAL
async def test_cancelled_open_stream_propagates_cancellation(driver, transactional):
    # when
    outcome = await cancel_open_stream(driver, transactional=transactional)
    # then
    assert outcome.cancelled is True


@TRANSACTIONAL
async def test_cancelled_open_stream_discards_the_connection(driver, transactional):
    # when
    outcome = await cancel_open_stream(driver, transactional=transactional)
    # then
    assert outcome.old_backend_ended is True


@TRANSACTIONAL
async def test_cancelled_open_stream_leaves_no_cursor_for_the_next_borrower(driver, transactional):
    # when
    outcome = await cancel_open_stream(driver, transactional=transactional)
    # then
    assert outcome.fresh_cursors == []


@TRANSACTIONAL
async def test_pool_settles_after_cancelled_stream(single_driver, transactional):
    # when
    outcome = await cancel_open_stream(single_driver, transactional=transactional)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_cancel_during_stream_fetch_discards_the_connection(driver):
    # when
    outcome = await cancel_stream_fetch(driver)
    # then
    assert outcome.cancelled is True
    assert outcome.old_backend_ended is True


async def test_cancel_during_stream_fetch_leaves_no_cursor_for_the_next_borrower(driver):
    # when
    outcome = await cancel_stream_fetch(driver)
    # then
    assert outcome.fresh_cursors == []


async def test_cancel_during_commit_discards_the_connection_and_loses_the_row(driver, scratch):
    # given
    await install_slow_commit(scratch)
    # when
    outcome = await cancel_commit(driver, scratch)
    # then
    assert outcome.cancelled is True
    assert outcome.old_backend_ended is True
    assert outcome.rows_left == 0


async def test_cancel_during_commit_replaces_the_connection(single_driver, scratch):
    # given
    await install_slow_commit(scratch)
    # when
    outcome = await cancel_commit(single_driver, scratch)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.fresh_pid != outcome.old_pid


async def test_cancel_during_cursor_close_discards_the_backend(driver):
    # when
    outcome = await cancel_stream_close(driver)
    # then
    assert outcome.cancelled is True
    assert outcome.old_backend_ended is True


async def test_cancel_during_cursor_close_leaves_no_held_cursor_for_the_next_borrower(driver):
    # when
    outcome = await cancel_stream_close(driver)
    # then
    assert outcome.fresh_cursors == []


async def test_cancellation_is_not_masked_when_cleanup_fails(driver):
    # when
    cancelled = await cancel_after_own_backend_killed(driver)
    # then
    assert cancelled is True


async def test_cancellation_during_recovery_still_propagates_as_cancelled_error(single_driver):
    """Characterisation test: it documents the behaviour but cannot catch a regression, because
    the discard path ends the task as cancelled whether or not the second cancel is re-raised."""
    # when
    cancelled = await cancel_again_during_recovery(single_driver)
    # then
    assert cancelled is True


async def test_rollback_after_a_deadline_discarded_the_connection_is_a_no_op(driver):
    # when
    outcome = await rollback_after_timeout_discard(driver)
    # then
    assert outcome.timed_out is True
    assert outcome.rollback_error is None


async def test_cancelled_raw_begin_is_rolled_back(single_driver, scratch):
    # when
    outcome = await cancel_in_raw_begin(single_driver, scratch)
    # then
    assert outcome.cancelled is True
    assert outcome.rows_after == []
    assert outcome.in_transaction_after is False


async def test_cancelled_raw_begin_keeps_the_same_backend(single_driver, scratch):
    # when
    outcome = await cancel_in_raw_begin(single_driver, scratch)
    # then
    assert outcome.pid_after == outcome.pid_before
