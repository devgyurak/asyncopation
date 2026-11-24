"""Pool lifecycle, capabilities and cancellation safety."""

import pytest

from asyncopation.drivers.base import Capabilities, PoolStats
from tests.support.drivers import (
    POSTGRES_ONLY,
    backend_ends,
    backend_pid,
    build_driver,
    cancel_during_sleep,
    cancel_waiting_borrower,
    concurrent_backend_pids,
    server_version_tuple,
)

pytestmark = POSTGRES_ONLY


def test_capabilities_before_open_raise_runtime_error(django_db_setup):
    # given
    driver = build_driver()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_capabilities_report_server_version(driver):
    # given
    expected = await server_version_tuple()
    # when
    capabilities = driver.capabilities
    # then
    assert isinstance(capabilities, Capabilities)
    assert capabilities.server_version[:2] == expected
    assert capabilities.server_side_cursors is True


async def test_close_terminates_pooled_connections(django_db_setup):
    # given
    pool = {"min_size": 1, "max_size": 1}
    driver = build_driver(pool=pool)
    await driver.open()
    async with driver.connection() as conn:
        pid = await backend_pid(conn)
    # when
    await driver.close()
    # then
    assert await backend_ends(pid)


async def test_pool_stats_count_idle_connections_after_open(counted_driver):
    # when
    stats = counted_driver.pool_stats()
    # then
    assert stats == PoolStats(in_use=0, idle=2, waiting=0)


async def test_pool_stats_count_borrowed_connection_as_in_use(counted_driver):
    # given
    async with counted_driver.connection():
        # when
        during = counted_driver.pool_stats()
    # then
    assert during == PoolStats(in_use=1, idle=1, waiting=0)


async def test_pool_stats_return_to_idle_after_the_connection_is_released(counted_driver):
    # given
    async with counted_driver.connection():
        pass
    # when
    after = counted_driver.pool_stats()
    # then
    assert after == PoolStats(in_use=0, idle=2, waiting=0)


async def test_cancelled_query_propagates_cancelled_error(driver):
    # when
    outcome = await cancel_during_sleep(driver)
    # then
    assert outcome.cancelled is True


async def test_cancelled_query_leaves_pool_stats_unchanged(driver):
    # when
    outcome = await cancel_during_sleep(driver)
    # then
    assert outcome.after == outcome.before
    assert outcome.after.in_use == 0


async def test_cancelled_query_stops_running_on_the_server(driver):
    # when
    outcome = await cancel_during_sleep(driver)
    # then
    assert outcome.statement_stopped is True


@pytest.mark.parametrize("transactional", [False, True], ids=["autocommit", "transaction"])
async def test_connection_borrowed_after_cancellation_is_healthy(driver, transactional):
    # when
    outcome = await cancel_during_sleep(driver, transactional=transactional)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.next_in_transaction is False


async def test_cancelled_waiter_on_saturated_pool_is_cancelled(single_driver):
    # when
    outcome = await cancel_waiting_borrower(single_driver)
    # then
    assert outcome.waiting_seen == 1
    assert outcome.cancelled is True


async def test_cancelled_waiter_does_not_leak_the_connection(single_driver):
    # when
    outcome = await cancel_waiting_borrower(single_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.reusable_rows == [(1,)]


async def test_concurrent_borrowers_get_distinct_connections(driver):
    # when
    pids = await concurrent_backend_pids(driver, 2)
    # then
    assert len(set(pids)) == 2
