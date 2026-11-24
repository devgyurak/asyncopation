"""Drift tests: the ported PostgreSQL connection logic still matches Django's (ADR-0007)."""

import pytest
from psycopg import IsolationLevel

from tests.support.drivers import (
    TzCase,
    compare_conn_kwargs,
    cursor_base_names,
    leftover_option_keys,
    pool_kwargs_for,
    quote_pair,
    timezone_name_for,
    timezone_pair,
)

FULL_OPTIONS = {
    "isolation_level": IsolationLevel.SERIALIZABLE,
    "assume_role": "some_role",
    "pool": {"min_size": 2, "max_size": 3},
    "server_side_binding": True,
    "prepare_threshold": 3,
    "client_encoding": "latin1",
    "connect_timeout": 7,
}


@pytest.mark.parametrize(
    ("name", "options"),
    [
        pytest.param("mydb", {}, id="name"),
        pytest.param(None, {}, id="name-none"),
        pytest.param("", {"service": "svc"}, id="service"),
        pytest.param("mydb", FULL_OPTIONS, id="all-options"),
        pytest.param("mydb", {"prepare_threshold": 0}, id="prepare-threshold-zero"),
    ],
)
def test_connection_params_match_django(name, options):
    # when
    comparison = compare_conn_kwargs(name, options)
    # then
    assert comparison.differences() == {}


@pytest.mark.parametrize(
    "options",
    [pytest.param({}, id="default"), pytest.param({"server_side_binding": True}, id="server-side")],
)
def test_cursor_factory_is_the_async_counterpart_of_djangos(options):
    # when
    comparison = compare_conn_kwargs("mydb", options)
    # then
    assert comparison.cursor_factories_correspond()


def test_options_django_consumes_are_not_passed_to_the_connection():
    # when
    leftovers = leftover_option_keys(FULL_OPTIONS)
    # then
    assert leftovers == []


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(TzCase(True, None, "UTC"), id="use-tz-utc"),
        pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="use-tz-db-seoul"),
        pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
        pytest.param(TzCase(False, None, "UTC"), id="no-tz-utc"),
    ],
)
def test_resolve_timezone_matches_django_wrapper(case):
    # when
    ours, reference = timezone_pair(case)
    # then
    assert ours == reference


@pytest.mark.parametrize("name", ["sp_1", '"already"', "with space", "UPPER"])
def test_quote_matches_django_quote_name(name):
    # when
    ours, reference = quote_pair(name)
    # then
    assert ours == reference


def test_stream_cursor_bases_mirror_djangos_server_side_cursor():
    # when
    ours, reference = cursor_base_names()
    # then
    assert ours == reference


def test_health_checks_add_a_pool_check_callback():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": True}, None)
    # then
    assert callable(kwargs["check"])


def test_pool_has_no_check_callback_by_default():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": False}, None)
    # then
    assert "check" not in kwargs


def test_large_min_size_raises_max_size_with_it():
    # when
    kwargs = pool_kwargs_for({}, {"min_size": 20})
    # then
    assert kwargs["max_size"] >= 20


def test_empty_time_zone_leaves_no_timezone_name_to_apply():
    # when
    name = timezone_name_for(TzCase(False, None, ""))
    # then
    assert not name
