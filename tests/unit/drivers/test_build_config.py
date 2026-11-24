"""`build_config` validation and pool option handling (pure, no I/O)."""

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


@pytest.mark.parametrize(
    "option",
    [
        pytest.param("name", id="name"),
        pytest.param("check", id="check"),
        pytest.param("reset", id="reset"),
        pytest.param("configure", id="configure"),
        pytest.param("connection_class", id="connection-class"),
        pytest.param("reconnect_failed", id="reconnect-failed"),
    ],
)
def test_pool_options_owned_by_the_driver_are_rejected(option):
    # when
    error = config_error({"pool": {option: noop_hook}})
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        pytest.param("close_returns", True, id="close-returns"),
        pytest.param("open", False, id="open"),
        pytest.param("kwargs", {}, id="kwargs"),
        pytest.param("conninfo", "dbname=x", id="conninfo"),
    ],
)
def test_pool_options_that_would_break_discarding_or_collide_with_the_driver_are_rejected(
    option, value
):
    # when
    error = config_error({"pool": {option: value}})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_callable_pool_option_values_are_rejected():
    # when
    error = config_error({"pool": {"min_size": 1, "anything": noop_hook}})
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_cursor_factory_option_is_rejected():
    # when
    error = config_error({"cursor_factory": noop_hook})
    # then
    assert isinstance(error, ImproperlyConfigured)


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
