"""A cancelled statement discards its connection (asyncmy closes it) and the pool recovers."""

import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import (
    cancel_during_sleep,
    rollback_after_cancel_in_transaction,
)

pytestmark = MYSQL_FAMILY

TRANSACTIONAL = pytest.mark.parametrize("transactional", [False, True], ids=["autocommit", "txn"])


@TRANSACTIONAL
async def test_cancelled_statement_propagates_cancelled_error(driver, transactional):
    # when
    outcome = await cancel_during_sleep(driver, transactional=transactional)
    # then
    assert outcome.cancelled is True


@TRANSACTIONAL
async def test_cancelled_statement_discards_the_connection(single_driver, transactional):
    # when
    outcome = await cancel_during_sleep(single_driver, transactional=transactional)
    # then
    assert outcome.next_id != outcome.held_id


@TRANSACTIONAL
async def test_cancelled_statement_leaves_nothing_in_use(driver, transactional):
    # when
    outcome = await cancel_during_sleep(driver, transactional=transactional)
    # then
    assert outcome.after.in_use == 0
    assert outcome.after.waiting == 0


@TRANSACTIONAL
async def test_pool_refills_after_a_cancelled_statement(single_driver, transactional):
    # when
    outcome = await cancel_during_sleep(single_driver, transactional=transactional)
    # then
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)


@TRANSACTIONAL
async def test_connection_borrowed_after_cancellation_is_healthy(driver, transactional):
    # when
    outcome = await cancel_during_sleep(driver, transactional=transactional)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.next_autocommit == 1


async def test_rollback_after_a_cancel_inside_a_transaction_is_a_no_op(single_driver):
    # when
    outcome = await rollback_after_cancel_in_transaction(single_driver)
    # then
    assert outcome.rollback_error is None


async def test_the_next_borrow_after_a_rollback_following_a_cancel_is_healthy(single_driver):
    # when
    outcome = await rollback_after_cancel_in_transaction(single_driver)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.next_autocommit == 1
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)
