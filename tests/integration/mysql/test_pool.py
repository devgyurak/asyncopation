"""Pool sizes, stats, acquire timeout, waiting limit, health checks and close."""

import pytest
from django.db import InterfaceError, OperationalError

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import MYSQL_FAMILY, build_my_driver, open_my_driver, settled_stats
from tests.support.mysql_scenarios import (
    acquire_while_saturated,
    borrow_after_idle_thread_killed,
    close_with_leaked_borrow,
    concurrent_connection_ids,
    kill_then_query,
    overflow_waiting,
    released_connection_error,
    rows_of,
    stats_with_a_waiter,
)

pytestmark = MYSQL_FAMILY


async def test_pool_stats_count_idle_connections_after_open(counted_driver):
    # when
    stats = await settled_stats(counted_driver, min_idle=2)
    # then
    assert stats == PoolStats(in_use=0, idle=2, waiting=0)


async def test_elastic_pool_opens_without_connections(elastic_driver):
    # when
    stats = elastic_driver.pool_stats()
    # then
    assert stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_pool_stats_count_a_borrowed_connection_as_in_use(counted_driver):
    # given
    await settled_stats(counted_driver, min_idle=2)
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
    stats = await settled_stats(counted_driver, min_idle=2)
    # then
    assert stats == PoolStats(in_use=0, idle=2, waiting=0)


async def test_pool_stats_count_a_task_waiting_for_the_only_connection(single_driver):
    # when
    outcome = await stats_with_a_waiter(single_driver)
    # then
    assert outcome.during.waiting == 1
    assert outcome.during.in_use == 1


async def test_the_waiter_is_served_when_the_connection_is_released(single_driver):
    # when
    outcome = await stats_with_a_waiter(single_driver)
    # then
    assert outcome.error is None
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_second_open_is_a_no_op(counted_driver):
    # given
    capabilities = counted_driver.capabilities
    # when
    await counted_driver.open()
    # then
    assert counted_driver.capabilities == capabilities
    assert await settled_stats(counted_driver, min_idle=2) == PoolStats(0, 2, 0)


async def test_pool_stats_are_zero_after_close(django_db_setup):
    # given
    driver = build_my_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    # when
    await driver.close()
    # then
    assert driver.pool_stats() == PoolStats(in_use=0, idle=0, waiting=0)


async def test_capabilities_after_close_raise_runtime_error(django_db_setup):
    # given
    driver = build_my_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_connection_after_close_raises_runtime_error(django_db_setup):
    # given
    driver = build_my_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


async def test_acquire_timeout_raises_a_django_operational_error(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert type(outcome.error) is OperationalError


async def test_acquire_timeout_is_not_a_disconnect(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert tight_driver.is_disconnect(outcome.error) is False


async def test_acquire_timeout_waits_about_the_configured_time(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert 0.1 <= outcome.elapsed < 3


async def test_acquire_timeout_leaves_the_pool_unchanged(tight_driver):
    # when
    outcome = await acquire_while_saturated(tight_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_max_waiting_overflow_raises_operational_error(bounded_queue_driver):
    # when
    outcome = await overflow_waiting(bounded_queue_driver)
    # then
    assert type(outcome.error) is OperationalError


async def test_max_waiting_overflow_is_refused_at_once(bounded_queue_driver):
    # when
    outcome = await overflow_waiting(bounded_queue_driver)
    # then
    assert outcome.elapsed < 1


async def test_max_waiting_overflow_leaves_the_pool_unchanged(bounded_queue_driver):
    # when
    outcome = await overflow_waiting(bounded_queue_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_health_check_replaces_an_idle_connection_killed_between_borrows(readonly_db):
    # given: the unit tests (test_mysql_borrow.py) prove the check itself; asyncmy replaces dead
    # connections anyway, so this shows only the end result
    # when
    outcome = await borrow_after_idle_thread_killed(health_checks=True)
    # then
    assert outcome.error is None
    assert outcome.rows == [(1,)]
    assert outcome.next_id != outcome.killed_id


async def test_without_health_checks_asyncmy_still_replaces_a_connection_killed_while_idle(
    readonly_db,
):
    # when
    outcome = await borrow_after_idle_thread_killed(health_checks=False)
    # then
    assert outcome.error is None
    assert outcome.rows == [(1,)]
    assert outcome.next_id != outcome.killed_id


async def test_a_killed_connection_raises_a_django_operational_error(driver):
    # when
    outcome = await kill_then_query(driver)
    # then
    assert type(outcome.error) is OperationalError


async def test_a_killed_connection_is_not_returned_to_the_pool(driver):
    # when
    outcome = await kill_then_query(driver)
    # then
    assert outcome.next_id != outcome.killed_id
    assert outcome.next_rows == [(1,)]


async def test_a_killed_connection_leaves_nothing_in_use(driver):
    # when
    outcome = await kill_then_query(driver)
    # then
    assert outcome.after.in_use == 0
    assert outcome.after.waiting == 0


@pytest.fixture(scope="module")
async def leaked_close(django_db_setup: None):
    """One `close()` with a leaked borrow (it waits its 5 s bound), shared by two tests."""
    return await close_with_leaked_borrow()


async def test_close_with_a_leaked_borrow_waits_its_bound_then_terminates(leaked_close):
    # when
    result = leaked_close.result
    # then
    assert result.error is None
    assert 4 <= result.elapsed < 8


async def test_close_with_a_leaked_borrow_leaves_zero_pool_stats(leaked_close):
    # when
    stats = leaked_close.stats
    # then
    assert stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_concurrent_borrowers_get_distinct_connections(driver):
    # when
    ids = await concurrent_connection_ids(driver)
    # then
    assert len(set(ids)) == 2


async def test_pool_serves_queries_without_a_pool_option(django_db_setup):
    # given
    async with open_my_driver() as driver:
        # when
        rows = await rows_of(driver, "SELECT 1")
    # then
    assert rows == [(1,)]


@pytest.mark.parametrize(
    "operation",
    [
        "fetch_all",
        "execute",
        "execute_many",
        "stream",
        "begin",
        "commit",
        "rollback",
        "savepoint",
        "release_savepoint",
        "rollback_to_savepoint",
    ],
)
async def test_a_released_connection_refuses_every_operation(driver, operation):
    # when
    error = await released_connection_error(driver, operation)
    # then
    assert type(error) is InterfaceError
