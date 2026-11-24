"""Pool lifecycle, capabilities and cancellation safety of the pool."""

import psycopg
import pytest
from django.db import InterfaceError, OperationalError

from asyncopation.drivers.base import Capabilities, PoolStats
from tests.support.drivers import (
    POSTGRES_ONLY,
    backend_ends,
    backend_pid,
    borrow_after_idle_backend_killed,
    build_driver,
    cancel_during_sleep,
    cancel_waiting_borrower,
    concurrent_backend_pids,
    connect_calls_during_open,
    open_driver,
    raw_error_in_stream_body,
    release_and_cancel_waiter_together,
    released_connection_error,
    server_version_tuple,
)

pytestmark = POSTGRES_ONLY

RELEASED_OPERATIONS = [
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
]


async def test_capabilities_report_server_version(driver):
    # given
    expected = await server_version_tuple()
    # when
    capabilities = driver.capabilities
    # then
    assert isinstance(capabilities, Capabilities)
    assert capabilities.server_version[:2] == expected
    assert capabilities.server_side_cursors is True


async def test_second_open_is_a_no_op(counted_driver):
    # given
    capabilities = counted_driver.capabilities
    # when
    await counted_driver.open()
    # then
    assert counted_driver.pool_stats() == PoolStats(in_use=0, idle=2, waiting=0)
    assert counted_driver.capabilities == capabilities


async def test_pool_stats_are_zero_after_close(django_db_setup):
    # given
    driver = build_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    # when
    await driver.close()
    # then
    assert driver.pool_stats() == PoolStats(in_use=0, idle=0, waiting=0)


async def test_capabilities_after_close_raise_runtime_error(django_db_setup):
    # given
    driver = build_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_connection_after_close_raises_runtime_error(django_db_setup):
    # given
    driver = build_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


async def test_close_terminates_pooled_connections(django_db_setup):
    # given
    driver = build_driver(pool={"min_size": 1, "max_size": 1})
    await driver.open()
    async with driver.connection() as conn:
        pid = await backend_pid(conn)
    # when
    await driver.close()
    # then
    assert await backend_ends(pid) is True


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


async def test_cancelled_query_keeps_the_same_backend(single_driver):
    # when
    outcome = await cancel_during_sleep(single_driver)
    # then
    assert outcome.pid_after == outcome.pid_before


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


async def test_cancelled_execute_many_stops_and_leaves_a_healthy_connection(single_driver):
    # when
    outcome = await cancel_during_sleep(single_driver, operation="execute_many")
    # then
    assert outcome.cancelled is True
    assert outcome.statement_stopped is True
    assert outcome.next_rows == [(1,)]


async def test_cancelled_waiter_on_saturated_pool_is_cancelled(single_driver):
    # when
    outcome = await cancel_waiting_borrower(single_driver)
    # then
    assert outcome.cancelled is True


async def test_cancelled_waiter_does_not_leak_the_connection(single_driver):
    # when
    outcome = await cancel_waiting_borrower(single_driver)
    # then
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)
    assert outcome.reusable_rows == [(1,)]


async def test_release_and_waiter_cancellation_in_the_same_tick_leak_nothing(single_driver):
    # when
    outcome = await release_and_cancel_waiter_together(single_driver)
    # then
    assert outcome.cancelled is True
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_release_and_waiter_cancellation_in_the_same_tick_keep_the_backend(single_driver):
    # when
    outcome = await release_and_cancel_waiter_together(single_driver)
    # then
    assert outcome.next_pid == outcome.holder_pid


async def test_concurrent_borrowers_get_distinct_connections(driver):
    # when
    pids = await concurrent_backend_pids(driver, 2)
    # then
    assert len(set(pids)) == 2


@pytest.mark.parametrize("operation", RELEASED_OPERATIONS)
async def test_released_connection_refuses_every_operation(driver, operation):
    # when
    error = await released_connection_error(driver, operation)
    # then
    assert type(error) is InterfaceError


async def test_raw_driver_exception_from_the_callers_body_propagates_unmapped(single_driver):
    # when
    outcome = await raw_error_in_stream_body(single_driver)
    # then
    assert type(outcome.error) is psycopg.OperationalError


async def test_raw_driver_exception_from_the_callers_body_keeps_the_connection(single_driver):
    # when
    outcome = await raw_error_in_stream_body(single_driver)
    # then
    assert outcome.pid_after == outcome.pid_before
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_health_check_replaces_an_idle_connection_killed_between_borrows(
    readonly_db, binding
):
    # when
    outcome = await borrow_after_idle_backend_killed(binding.options, health_checks=True)
    # then
    assert outcome.error is None
    assert outcome.rows == [(1,)]
    assert outcome.next_pid != outcome.killed_pid


async def test_without_health_checks_the_first_borrow_after_a_kill_fails_with_a_django_error(
    readonly_db, binding
):
    # when
    outcome = await borrow_after_idle_backend_killed(binding.options, health_checks=False)
    # then
    assert type(outcome.error) is OperationalError


async def test_without_health_checks_the_discarded_connection_is_replaced_for_the_next_borrow(
    readonly_db, binding
):
    # when
    outcome = await borrow_after_idle_backend_killed(binding.options, health_checks=False)
    # then
    assert outcome.retry_rows == [(1,)]


async def test_open_connects_with_the_configured_connection_class_for_probe_and_pool(
    django_db_setup,
):
    # when
    calls = await connect_calls_during_open()
    # then
    assert calls == 2


async def test_pool_without_connection_class_opens_and_serves_queries(django_db_setup):
    # given
    async with (
        open_driver(pool={"min_size": 1, "max_size": 1}) as driver,
        driver.connection() as conn,
    ):
        # when
        rows = await conn.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]
