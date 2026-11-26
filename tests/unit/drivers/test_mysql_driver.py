"""MySQLDriver behaviour that needs no database (built from literal settings)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.drivers.base import PoolStats
from asyncopation.drivers.mysql import MySQLDriver
from tests.support.drivers import close_before_open_and_twice
from tests.support.mysql_driver_drift import (
    construct_with_asyncio_and_sockets_forbidden,
    driver_from_config,
    my_config,
    offline_my_driver,
    sync_error,
)
from tests.support.mysql_errors import asyncmy_error, mapped


def test_capabilities_before_open_raise_runtime_error():
    # given
    driver = offline_my_driver()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


def test_server_data_before_open_raises_runtime_error():
    # given
    driver = offline_my_driver()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.server_data


async def test_connection_before_open_raises_runtime_error():
    # given
    driver = offline_my_driver()
    # when / then
    with pytest.raises(RuntimeError):
        async with driver.connection():
            pass


def test_pool_stats_are_zero_before_open():
    # given
    driver = offline_my_driver()
    # when
    stats = driver.pool_stats()
    # then
    assert stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_close_before_open_and_repeated_close_are_safe():
    # given
    driver = offline_my_driver()
    # when
    stats = await close_before_open_and_twice(driver)
    # then
    assert stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_capabilities_stay_unavailable_after_close_before_open():
    # given
    driver = offline_my_driver()
    await driver.close()
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


def test_construction_creates_no_asyncio_objects_and_does_no_io():
    # when
    driver = construct_with_asyncio_and_sockets_forbidden()
    # then
    assert isinstance(driver, MySQLDriver)


def test_the_constructor_takes_a_config():
    # when
    driver = driver_from_config(my_config())
    # then
    assert isinstance(driver, MySQLDriver)


def test_from_settings_rejects_invalid_pool_options_before_connecting():
    # when
    error = sync_error(
        lambda: offline_my_driver(None, ASYNCOPATION={"pool": {"min_size": 5, "max_size": 2}})
    )
    # then
    assert isinstance(error, ImproperlyConfigured)


def test_from_settings_rejects_unknown_options_before_connecting():
    # when
    error = sync_error(lambda: offline_my_driver({"ssl_mode": "REQUIRED"}))
    # then
    assert isinstance(error, ImproperlyConfigured)


@pytest.mark.parametrize("code", [1053, 2006, 2013, 2014, 2055, 1927, 3169])
def test_the_driver_classifies_disconnects_through_its_error_module(code):
    # given
    driver = offline_my_driver()
    # when
    result = driver.is_disconnect(mapped("OperationalError", code, "gone"))
    # then
    assert result is True


def test_the_driver_does_not_treat_a_unique_violation_as_a_disconnect():
    # given
    driver = offline_my_driver()
    # when
    result = driver.is_disconnect(asyncmy_error("IntegrityError", 1062, "dup"))
    # then
    assert result is False
