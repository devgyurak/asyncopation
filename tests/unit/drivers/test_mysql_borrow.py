"""`MySQLDriver.connection()` against a fake pool: health checks, a closing pool, pool kwargs."""

from django.db import OperationalError

from tests.support.mysql_fakes import (
    acquire_error_when_the_pool_is_closing,
    borrow_past_a_dead_idle_connection,
    borrow_with_a_cancelled_ping,
    create_pool_kwargs,
    open_with_server_version,
)


async def test_a_failing_ping_drops_the_connection_and_serves_the_next_one():
    # when
    outcome = await borrow_past_a_dead_idle_connection(health_checks=True)
    # then
    assert outcome.served is outcome.good


async def test_a_connection_that_failed_its_ping_is_closed_and_handed_back_to_the_pool():
    # when
    outcome = await borrow_past_a_dead_idle_connection(health_checks=True)
    # then
    assert outcome.bad.closed is True
    assert outcome.pool.released[0] is outcome.bad


async def test_the_health_check_pings_without_reconnecting():
    # when
    outcome = await borrow_past_a_dead_idle_connection(health_checks=True)
    # then
    assert outcome.bad.ping_calls == [False]
    assert outcome.good.ping_calls == [False]


async def test_ping_is_not_called_when_health_checks_are_off():
    # when
    outcome = await borrow_past_a_dead_idle_connection(health_checks=False)
    # then
    assert outcome.served is outcome.bad
    assert outcome.bad.ping_calls == []
    assert outcome.bad.closed is False


async def test_a_cancel_during_the_ping_drops_the_connection_and_propagates():
    # when
    outcome = await borrow_with_a_cancelled_ping()
    # then
    assert outcome.cancelled is True
    assert outcome.error is None
    assert outcome.raw.closed is True
    assert outcome.pool.released == [outcome.raw]


async def test_an_acquire_racing_close_raises_operational_error_naming_the_closed_pool():
    # when
    error = await acquire_error_when_the_pool_is_closing()
    # then
    assert type(error) is OperationalError
    assert "is closed" in str(error)


async def test_max_idle_reaches_asyncmy_as_pool_recycle():
    # when
    kwargs = await create_pool_kwargs({"max_idle": 600, "min_size": 2, "max_size": 5})
    # then
    assert kwargs["pool_recycle"] == 600
    assert (kwargs["minsize"], kwargs["maxsize"]) == (2, 5)


async def test_the_pool_runs_set_sql_auto_is_null_when_the_server_has_it_enabled():
    # when
    kwargs = await create_pool_kwargs({}, sql_auto_is_null=True)
    # then
    assert "SET SQL_AUTO_IS_NULL = 0" in kwargs["init_command"]


async def test_the_pool_does_not_run_it_when_the_server_has_it_disabled():
    # when
    kwargs = await create_pool_kwargs({}, sql_auto_is_null=False)
    # then
    assert "SQL_AUTO_IS_NULL" not in kwargs["init_command"]


async def test_a_server_version_that_cannot_be_parsed_creates_no_pool_and_leaves_the_driver_closed():
    # when
    error, pool_created, is_open = await open_with_server_version("garbage")
    # then
    assert error is not None
    assert "Unable to determine MySQL version" in str(error)
    assert "garbage" in str(error)
    assert pool_created is False
    assert is_open is False
