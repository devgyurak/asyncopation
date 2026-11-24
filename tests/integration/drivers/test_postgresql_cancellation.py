"""Cancellation safety: transactions, open streams and teardown (ADR-0001 rule 6)."""

import pytest
from django.db import InterfaceError, OperationalError

from tests.support.drivers import (
    POSTGRES_ONLY,
    cancel_after_own_backend_killed,
    cancel_open_stream,
    cancel_write_in_transaction,
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
    assert isinstance(outcome.reuse_error, OperationalError | InterfaceError)
    assert outcome.old_backend_ended is True


async def test_cancelled_statement_in_transaction_frees_the_pool_slot(driver, scratch):
    # when
    outcome = await cancel_write_in_transaction(driver, scratch)
    # then
    assert outcome.after.in_use == 0
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
    assert isinstance(outcome.reuse_error, OperationalError | InterfaceError)
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
    assert outcome.after.in_use == 0
    assert outcome.after.waiting == 0


async def test_cancellation_is_not_masked_when_cleanup_fails(driver):
    # when
    cancelled = await cancel_after_own_backend_killed(driver)
    # then
    assert cancelled is True
