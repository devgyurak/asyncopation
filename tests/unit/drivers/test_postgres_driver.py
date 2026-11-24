"""PostgresDriver behaviour that needs no database (built from literal settings)."""

import psycopg
import psycopg_pool
import pytest

from asyncopation.drivers.base import PoolStats
from tests.support.drivers import (
    close_before_open_and_twice,
    custom_subclass,
    error_with_diag,
    lookup_error,
    mapped_from,
    offline_driver,
    open_error,
)


def test_capabilities_before_open_raise_runtime_error():
    # given
    driver = offline_driver()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        pytest.param(psycopg.errors.AdminShutdown("x"), True, id="raw-admin-shutdown"),
        pytest.param(psycopg.errors.UniqueViolation("x"), False, id="raw-unique"),
        pytest.param(
            psycopg.OperationalError("consuming input failed"), True, id="raw-no-sqlstate"
        ),
        pytest.param(psycopg.errors.QueryCanceled("x"), False, id="raw-query-canceled"),
        pytest.param(psycopg_pool.PoolTimeout("x"), False, id="raw-pool-timeout"),
        pytest.param(psycopg.InterfaceError("connection is closed"), False, id="raw-interface"),
        pytest.param(ValueError("x"), False, id="foreign"),
        pytest.param(mapped_from(psycopg.errors.AdminShutdown("x")), True, id="mapped-admin"),
        pytest.param(mapped_from(psycopg.errors.UniqueViolation("x")), False, id="mapped-unique"),
        pytest.param(
            mapped_from(psycopg.OperationalError("consuming input failed")),
            True,
            id="mapped-no-sqlstate",
        ),
        pytest.param(mapped_from(psycopg.errors.QueryCanceled("x")), False, id="mapped-canceled"),
        pytest.param(mapped_from(psycopg_pool.PoolTimeout("x")), False, id="mapped-pool-timeout"),
    ],
)
def test_is_disconnect_classifies_exceptions(exc, expected):
    # given
    driver = offline_driver()
    # when
    result = driver.is_disconnect(exc)
    # then
    assert result is expected


@pytest.mark.parametrize("sqlstate", ["57P01", "57P02", "57P03", "57P04", "57P05", "25P03"])
def test_is_disconnect_is_true_for_server_side_session_termination(sqlstate):
    # given
    driver = offline_driver()
    # when
    result = driver.is_disconnect(lookup_error(sqlstate))
    # then
    assert result is True


@pytest.mark.parametrize(
    "base",
    [psycopg.OperationalError, psycopg.errors.InternalError_, psycopg.errors.DatabaseError],
    ids=["operational", "internal", "database"],
)
def test_is_disconnect_is_true_for_any_fatal_severity(base):
    # given
    driver = offline_driver()
    exc = error_with_diag(base, severity=b"FATAL", sqlstate=None)
    # when
    result = driver.is_disconnect(exc)
    # then
    assert result is True


def test_is_disconnect_is_false_for_a_non_fatal_error_severity():
    # given
    driver = offline_driver()
    exc = error_with_diag(psycopg.OperationalError, severity=b"ERROR", sqlstate=b"40001")
    # when
    result = driver.is_disconnect(exc)
    # then
    assert result is False


@pytest.mark.parametrize("sqlstate", ["08000", "08003", "08006"])
def test_is_disconnect_is_true_for_raw_connection_exception_codes(sqlstate):
    # given
    driver = offline_driver()
    # when
    result = driver.is_disconnect(lookup_error(sqlstate))
    # then
    assert result is True


@pytest.mark.parametrize("sqlstate", ["08000", "08003", "08006"])
def test_is_disconnect_is_true_for_mapped_connection_exception_codes(sqlstate):
    # given
    driver = offline_driver()
    # when
    result = driver.is_disconnect(mapped_from(lookup_error(sqlstate)))
    # then
    assert result is True


@pytest.mark.parametrize("sqlstate", [b"08000", b"08003", b"08006"])
def test_is_disconnect_is_true_for_an_operational_error_subclass_with_such_a_code(sqlstate):
    # given
    driver = offline_driver()
    exc = error_with_diag(
        custom_subclass(psycopg.OperationalError), severity=b"ERROR", sqlstate=sqlstate
    )
    # when
    result = driver.is_disconnect(exc)
    # then
    assert result is True


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(psycopg_pool.PoolClosed("x"), id="raw-pool-closed"),
        pytest.param(psycopg_pool.TooManyRequests("x"), id="raw-too-many-requests"),
        pytest.param(mapped_from(psycopg_pool.PoolClosed("x")), id="mapped-pool-closed"),
        pytest.param(mapped_from(psycopg_pool.TooManyRequests("x")), id="mapped-too-many"),
    ],
)
def test_pool_state_errors_are_not_disconnects(exc):
    # given
    driver = offline_driver()
    # when
    result = driver.is_disconnect(exc)
    # then
    assert result is False


async def test_connection_before_open_raises_runtime_error():
    # given
    driver = offline_driver()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


async def test_close_before_open_and_repeated_close_are_safe():
    # given
    driver = offline_driver()
    # when
    stats = await close_before_open_and_twice(driver)
    # then
    assert stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_capabilities_stay_unavailable_after_close_before_open():
    # given
    driver = offline_driver()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


async def test_min_size_above_max_size_makes_open_raise_value_error_before_connecting():
    # given: the host is unreachable, so any connection attempt would fail differently
    driver = offline_driver({"pool": {"min_size": 5, "max_size": 2}})
    # when
    error = await open_error(driver)
    # then
    assert type(error) is ValueError
