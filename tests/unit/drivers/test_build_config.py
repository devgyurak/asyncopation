"""`build_config` validation and pool option handling (pure, no I/O)."""

from collections import OrderedDict
from types import MappingProxyType

import psycopg
import pytest
from django.core.exceptions import ImproperlyConfigured

from tests.support.drivers import (
    TzCase,
    config_error,
    noop_hook,
    pool_kwargs_for,
    pool_true_matches_defaults,
    timezone_name_for,
)


class NoopClass:
    """A class is callable too, but not a connection class."""


class SubclassedConnection(psycopg.AsyncConnection):
    """A legitimate `connection_class`."""


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param(None, id="none"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param({}, id="empty-dict"),
        pytest.param(MappingProxyType({"min_size": 2, "max_size": 4}), id="mapping-proxy"),
        pytest.param(OrderedDict(min_size=1), id="ordered-dict"),
    ],
)
def test_pool_option_accepts_none_bool_and_any_mapping(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert error is None


@pytest.mark.parametrize(
    "pool",
    [pytest.param("min_size=2", id="str"), pytest.param(5, id="int"), pytest.param([1], id="list")],
)
def test_pool_option_of_any_other_type_is_rejected(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize("key", ["check", "reset", "configure", "reconnect_failed"])
@pytest.mark.parametrize("value", [noop_hook, NoopClass], ids=["function", "class"])
def test_callable_pool_values_are_rejected(key, value):
    # when
    error = config_error({"pool": {key: value}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    "value",
    [True, 1, "yes", "false"],
    ids=["true", "one", "yes", "false-string"],
)
def test_close_returns_other_than_false_is_rejected(value):
    # when
    error = config_error({"pool": {"close_returns": value}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"close_returns": False}, id="close-returns-false"),
        pytest.param({"check": None}, id="check-none"),
        pytest.param({"reconnect_failed": None}, id="reconnect-failed-none"),
        pytest.param({"max_lifetime": 60}, id="max-lifetime"),
        pytest.param({"max_idle": 30.0}, id="max-idle"),
        pytest.param({"num_workers": 2}, id="num-workers"),
        pytest.param({"timeout": 5}, id="timeout"),
        pytest.param({"reconnect_timeout": 60}, id="reconnect-timeout"),
        pytest.param({"max_waiting": 0}, id="max-waiting"),
    ],
)
def test_plain_pool_parameters_are_accepted(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert error is None


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"name": "x"}, id="name"),
        pytest.param({"configure": None}, id="configure"),
        pytest.param({"kwargs": {}}, id="kwargs"),
        pytest.param({"conninfo": "x"}, id="conninfo"),
        pytest.param({"open": False}, id="open"),
    ],
)
def test_pool_parameters_the_driver_passes_itself_are_rejected_by_name(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_unknown_pool_key_is_rejected_before_any_connection():
    # when
    error = config_error({"pool": {"min_sise": 2}})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_cursor_factory_option_is_rejected():
    # when
    error = config_error({"cursor_factory": noop_hook})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_pool_mapping_with_non_string_keys_is_rejected():
    # when
    error = config_error({"pool": {1: 2, "a": 3}})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_min_size_alone_is_accepted_and_the_default_max_size_follows_it():
    # when
    kwargs = pool_kwargs_for({}, {"min_size": 20})
    # then
    assert (kwargs["min_size"], kwargs["max_size"]) == (20, 20)


def test_max_size_alone_keeps_the_default_min_size():
    # when
    kwargs = pool_kwargs_for({}, {"max_size": 3})
    # then
    assert (kwargs["min_size"], kwargs["max_size"]) == (1, 3)


def test_pool_sizes_default_when_neither_is_given():
    # when
    kwargs = pool_kwargs_for({}, {"timeout": 5})
    # then
    assert (kwargs["min_size"], kwargs["max_size"]) == (1, 10)


def test_explicit_check_none_is_respected_with_health_checks_on():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": True}, {"check": None})
    # then
    assert kwargs.get("check") is None


def test_health_checks_add_the_check_when_none_is_configured():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": True}, {"timeout": 5})
    # then
    assert callable(kwargs["check"])


def test_pool_true_means_default_pool_options():
    # when
    same = pool_true_matches_defaults()
    # then
    assert same is True


def test_health_checks_add_a_pool_check_callback():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": True})
    # then
    assert callable(kwargs["check"])


def test_pool_has_no_check_callback_by_default():
    # when
    kwargs = pool_kwargs_for({"CONN_HEALTH_CHECKS": False})
    # then
    assert "check" not in kwargs


def test_empty_time_zone_leaves_no_timezone_name_to_apply():
    # when
    name = timezone_name_for(TzCase(False, None, ""))
    # then
    assert not name


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"min_size": "5"}, id="min-str"),
        pytest.param({"min_size": None}, id="min-none"),
        pytest.param({"min_size": 2.5}, id="min-float"),
        pytest.param({"max_size": "7"}, id="max-str"),
        pytest.param({"min_size": True}, id="min-bool"),
        pytest.param({"max_size": 2.5}, id="max-float"),
    ],
)
def test_pool_sizes_of_the_wrong_type_are_rejected(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    "pool",
    [
        pytest.param({"max_size": None}, id="max-none"),
        pytest.param({"min_size": 0}, id="min-zero"),
        pytest.param({"min_size": 3, "max_size": 3}, id="equal"),
        pytest.param({"min_size": 2, "max_size": 8}, id="both"),
    ],
)
def test_integer_pool_sizes_and_unset_max_size_are_accepted(pool):
    # when
    error = config_error({"pool": pool})
    # then
    assert error is None


FLOAT_OPTIONS = ["timeout", "max_lifetime", "max_idle", "reconnect_timeout"]
INT_OPTIONS = ["max_waiting", "num_workers"]
NUMERIC_OPTIONS = [*FLOAT_OPTIONS, *INT_OPTIONS]


@pytest.mark.parametrize("option", NUMERIC_OPTIONS)
@pytest.mark.parametrize(
    "value",
    ["5", True, [1], None],
    ids=["str", "bool", "list", "none"],
)
def test_numeric_pool_options_of_the_wrong_type_are_rejected(option, value):
    # when
    error = config_error({"pool": {option: value}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize("option", NUMERIC_OPTIONS)
def test_numeric_pool_options_accept_ints(option):
    # when
    error = config_error({"pool": {option: 5}})
    # then
    assert error is None


@pytest.mark.parametrize("option", FLOAT_OPTIONS)
def test_float_pool_options_accept_floats(option):
    # when
    error = config_error({"pool": {option: 2.5}})
    # then
    assert error is None


@pytest.mark.parametrize(
    ("option", "value"),
    [
        pytest.param("num_workers", 0, id="num-workers-zero"),
        pytest.param("num_workers", -1, id="num-workers-negative"),
        pytest.param("num_workers", 2.5, id="num-workers-float"),
        pytest.param("max_waiting", -1, id="max-waiting-negative"),
        pytest.param("max_waiting", 2.5, id="max-waiting-float"),
    ],
)
def test_count_pool_options_outside_their_range_are_rejected(option, value):
    # when
    error = config_error({"pool": {option: value}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        pytest.param("num_workers", 1, id="num-workers-one"),
        pytest.param("max_waiting", 0, id="max-waiting-zero-is-unlimited"),
    ],
)
def test_count_pool_options_at_their_lower_bound_are_accepted(option, value):
    # when
    error = config_error({"pool": {option: value}})
    # then
    assert error is None


@pytest.mark.parametrize(
    "connection_class",
    [
        pytest.param(psycopg.Connection, id="sync-connection"),
        pytest.param(noop_hook, id="function"),
        pytest.param(NoopClass, id="unrelated-class"),
        pytest.param("psycopg.AsyncConnection", id="dotted-string"),
        pytest.param(None, id="none"),
    ],
)
def test_pool_connection_class_must_be_an_async_connection_subclass(connection_class):
    # when
    error = config_error({"pool": {"connection_class": connection_class}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    "connection_class",
    [
        pytest.param(psycopg.AsyncConnection, id="async-connection"),
        pytest.param(SubclassedConnection, id="subclass"),
    ],
)
def test_pool_connection_class_accepts_async_connection_subclasses(connection_class):
    # when
    error = config_error({"pool": {"connection_class": connection_class}})
    # then
    assert error is None
