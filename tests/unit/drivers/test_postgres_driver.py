"""PostgresDriver behaviour that needs no database (built from literal settings)."""

import psycopg
import psycopg_pool
import pytest

from tests.support.drivers import (
    error_with_diag,
    lookup_error,
    mapped_from,
    offline_driver,
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
