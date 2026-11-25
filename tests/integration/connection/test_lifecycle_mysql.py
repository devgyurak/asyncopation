"""Lifecycle, registry and probe against a real MySQL/MariaDB (ADR-0004 Validation)."""

import asyncio

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import Error as DjangoDbError

from asyncopation.compile.server_info import MySQLServerInfo
from asyncopation.connection import close, registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from asyncopation.drivers.base import PoolStats
from tests.support.lifecycle.fakes import counts
from tests.support.lifecycle.helpers import aliases_of, distinct, failing, injected_aliases
from tests.support.lifecycle.mysql import (
    compile_attempts_with_injected_wrapper,
    fail_first_open_then_recover,
    pool_stats_of,
    pool_threads_during_setup_and_after_close,
    registry_and_sync_server_data,
    registry_and_sync_server_data_with_init_command,
)
from tests.support.lifecycle.postgres import ensure_after_rename
from tests.support.lifecycle.scenarios import ensure_many
from tests.support.mysql_env import MYSQL_FAMILY

pytestmark = MYSQL_FAMILY


async def test_setup_on_a_mysql_alias_opens_one_driver_for_the_running_loop(my_spy):
    # when
    await setup()
    # then
    assert registry.entries() == [("default", asyncio.get_running_loop())]


async def test_compiling_after_setup_makes_no_sync_connection_attempt(my_spy):
    # given
    await setup()
    # when
    attempts = compile_attempts_with_injected_wrapper("default")
    # then
    assert attempts == 0
    assert injected_aliases(my_spy) == ["default"]


async def test_concurrent_first_calls_without_setup_share_one_driver(my_spy):
    # when
    drivers = await ensure_many("default", 20)
    # then
    assert distinct(drivers) == 1


async def test_concurrent_first_calls_without_setup_open_and_probe_once(my_spy):
    # when
    await ensure_many("default", 20)
    # then
    assert counts(my_spy)["built"] == 1
    assert counts(my_spy)["opens"] == 1
    assert counts(my_spy)["probes"] == 1


async def test_the_probe_result_equals_djangos_sync_mysql_server_data(my_spy):
    # when
    result = await registry_and_sync_server_data()
    # then
    assert result.async_value == result.sync_value


async def test_the_probe_result_is_a_mysql_server_info(my_spy):
    # given
    await ensure_driver("default")
    # when
    info = registry.server_info("default")
    # then
    assert isinstance(info, MySQLServerInfo)


async def test_the_probe_result_reflects_an_init_command_that_changes_sql_mode(my_spy_env):
    # when
    result = await registry_and_sync_server_data_with_init_command("SET sql_mode='ANSI'")
    # then
    assert result.async_value == result.sync_value
    assert "ANSI_QUOTES" in result.async_value["sql_mode"]


async def test_the_probe_runs_once_per_process(my_spy):
    # given
    await ensure_driver("default")
    # when
    await ensure_driver("default")
    # then
    assert my_spy.probes == 1


async def test_failed_first_open_leaves_no_entry_and_no_server_info(my_spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert (outcome.entries_after_failure, outcome.server_info_after_failure) == ([], None)


async def test_failed_first_open_raises_a_django_database_error(my_spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert isinstance(outcome.error, DjangoDbError)


async def test_open_is_retried_with_the_corrected_config(my_spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert (
        aliases_of(outcome.entries_after_recovery),
        outcome.server_info_after_recovery is not None,
    ) == (["default"], True)


async def test_closing_the_driver_leaves_zero_pool_stats(my_spy):
    # given
    driver = await ensure_driver("default")
    # when
    await close()
    # then
    assert pool_stats_of(driver) == PoolStats(in_use=0, idle=0, waiting=0)


async def test_close_removes_the_entries_of_the_loop(my_spy):
    # given
    await setup()
    # when
    await close()
    # then
    assert registry.entries() == []


async def test_close_ends_every_server_thread_of_the_pool(my_spy):
    # when
    during, after = await pool_threads_during_setup_and_after_close()
    # then
    assert during >= 1
    assert after == 0


async def test_a_rename_after_configure_is_refused_instead_of_reaching_the_old_database(my_spy):
    # when / then
    with pytest.raises(ImproperlyConfigured, match="changed since configure"):
        await ensure_after_rename("default")


async def test_a_refused_rename_leaves_no_entry(my_spy):
    # given
    await failing(ensure_after_rename("default"), ImproperlyConfigured)
    # when
    entries = registry.entries()
    # then
    assert entries == []
