"""`server_data` and `capabilities` are read once by `open()` and equal Django's sync values."""

import dataclasses

import pytest

from asyncopation._django.mysql import parse_server_version
from asyncopation.drivers.base import Capabilities
from asyncopation.drivers.mysql.server_data import MySQLServerData
from tests.support.mysql_env import MYSQL_FAMILY, open_my_driver
from tests.support.mysql_reference import (
    compare_server_data,
    compare_server_version,
    sync_server_data,
)

pytestmark = MYSQL_FAMILY

ANSI = {"init_command": "SET sql_mode='ANSI'"}


async def test_capabilities_server_version_equals_django_mysql_version(readonly_db):
    # when
    result = await compare_server_version()
    # then
    assert result.async_value == result.sync_value


async def test_capabilities_server_version_is_parsed_from_the_server_data_version(driver):
    # when
    capabilities = driver.capabilities
    # then
    assert capabilities.server_version == parse_server_version(driver.server_data.version)


async def test_capabilities_carry_the_mysql_driver_facts(driver):
    # when
    capabilities = driver.capabilities
    # then
    assert isinstance(capabilities, Capabilities)
    assert capabilities.server_side_cursors is True
    assert capabilities.atomic_execute_many is False


async def test_server_data_equals_django_mysql_server_data_field_by_field(readonly_db):
    # when
    result = await compare_server_data()
    # then
    assert result.async_value == result.sync_value


async def test_server_data_is_a_value_with_exactly_the_django_keys(driver):
    # when
    data = driver.server_data
    # then
    assert isinstance(data, MySQLServerData)
    assert set(dataclasses.asdict(data)) == {
        "version",
        "sql_mode",
        "default_storage_engine",
        "sql_auto_is_null",
        "lower_case_table_names",
        "has_zoneinfo_database",
    }


async def test_server_data_reflects_an_init_command_that_changes_sql_mode(readonly_db):
    # when
    result = await compare_server_data(ANSI)
    # then
    assert result.async_value == result.sync_value
    assert "ANSI_QUOTES" in result.async_value["sql_mode"]


async def test_the_init_command_sql_mode_differs_from_the_default_server_data(readonly_db):
    # given
    default = await sync_server_data()
    # when
    async with open_my_driver(options=ANSI) as driver:
        ansi = driver.server_data
    # then
    assert ansi.sql_mode != default["sql_mode"]


async def test_second_open_keeps_the_server_data(counted_driver):
    # given
    data = counted_driver.server_data
    # when
    await counted_driver.open()
    # then
    assert counted_driver.server_data == data


async def test_server_data_after_close_raises_runtime_error(django_db_setup):
    # given
    async with open_my_driver(pool={"min_size": 1, "max_size": 1}) as driver:
        pass
    # when / then
    with pytest.raises(RuntimeError):
        _ = driver.server_data
