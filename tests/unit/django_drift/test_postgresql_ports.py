"""Drift: ported PostgreSQL connection logic still behaves like upstream Django (ADR-0007).

These tests import upstream Django directly (through tests/support); the driver ports are not
importable ledger entries (see docs/django-api-ledger.toml).
"""

import pytest
from psycopg import IsolationLevel

from tests.support.drivers import (
    TzCase,
    compare_conn_kwargs,
    cursor_base_names,
    drift_settings,
    leftover_option_keys,
    our_error,
    quote_pair,
    timezone_pair,
    upstream_error,
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
TZ_CASES = [
    pytest.param(TzCase(True, None, "UTC"), id="use-tz-utc"),
    pytest.param(TzCase(True, None, "Asia/Seoul"), id="use-tz-settings-seoul"),
    pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="use-tz-db-seoul"),
    pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
    pytest.param(TzCase(False, None, "UTC"), id="no-tz-utc"),
]


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


@pytest.mark.parametrize("case", TZ_CASES)
def test_resolve_timezone_matches_django_wrapper(case):
    # when
    ours, reference = timezone_pair(case)
    # then
    assert ours == reference


@pytest.mark.parametrize("case", TZ_CASES)
def test_connection_params_context_matches_django_for_every_time_zone_case(case):
    # when
    comparison = compare_conn_kwargs("mydb", {}, case)
    # then
    assert comparison.differences() == {}


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


@pytest.mark.parametrize(
    ("name", "options", "call"),
    [
        pytest.param("", {}, "get_connection_params", id="empty-name-without-service"),
        pytest.param("x" * 64, {}, "get_connection_params", id="name-too-long"),
        pytest.param(
            "db",
            {"isolation_level": 99},
            "get_new_connection",
            id="invalid-isolation-level",
        ),
    ],
)
def test_configuration_errors_match_django(name, options, call):
    # given
    settings_dict = drift_settings(name, options)
    # when
    ours = our_error(settings_dict)
    # then
    assert ours is not None
    assert ours == upstream_error(call, settings_dict)


def test_time_zone_with_use_tz_false_is_rejected_like_django():
    # given
    settings_dict = drift_settings("db", None, TIME_ZONE="UTC")
    # when
    ours = our_error(settings_dict, use_tz=False)
    # then
    assert ours is not None
    assert ours == upstream_error("check_settings", settings_dict, use_tz=False)
