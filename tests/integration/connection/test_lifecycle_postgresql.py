"""Lifecycle, registry and probe against a real PostgreSQL (ADR-0004 Validation)."""

import asyncio

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import Error as DjangoDbError

from asyncopation.compile.server_info import PostgreSQLServerInfo
from asyncopation.connection import close, configure, registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from harness.settings import VENDOR
from tests.support.drivers.admin import admin_scalar
from tests.support.lifecycle.fakes import counts
from tests.support.lifecycle.helpers import (
    aliases_of,
    distinct,
    failing,
    injected_aliases,
    last_prepared_name,
    pg_version_number,
    prepared_names,
)
from tests.support.lifecycle.loops import open_in_second_loop
from tests.support.lifecycle.postgres import (
    attempts_reading_pg_version_without_injection,
    compile_attempts_with_injected_wrapper,
    current_database_of_driver,
    ensure_after_rename,
    fail_first_open_then_recover,
    live_database_name,
    rename_then_restore,
    server_connections,
    server_connections_after_settling,
)
from tests.support.lifecycle.scenarios import ensure_many

pytestmark = pytest.mark.skipif(
    VENDOR != "postgresql",
    reason="MySQL/MariaDB drivers (asyncmy) arrive with issue #7; the lifecycle integration "
    "tests run on PostgreSQL only until then",
)


async def test_compiling_after_setup_makes_no_sync_connection_attempt(spy):
    # given
    await setup()
    # when
    attempts = compile_attempts_with_injected_wrapper("default")
    # then
    assert attempts == 0
    assert injected_aliases(spy) == ["default"]


async def test_concurrent_first_calls_without_setup_share_one_driver(spy):
    # when
    drivers = await ensure_many("default", 20)
    # then
    assert distinct(drivers) == 1


async def test_concurrent_first_calls_without_setup_open_and_probe_once(spy):
    # when
    await ensure_many("default", 20)
    # then
    assert counts(spy)["built"] == 1
    assert counts(spy)["opens"] == 1
    assert counts(spy)["probes"] == 1


async def test_second_event_loop_gets_its_own_driver(spy):
    # given
    main = await ensure_driver("default")
    # when
    second = await open_in_second_loop("default")
    # then
    assert second.driver is not main


async def test_second_event_loop_lists_two_entries_while_open(spy):
    # given
    await ensure_driver("default")
    # when
    second = await open_in_second_loop("default")
    # then
    assert len(second.entries_inside) == 2


async def test_server_info_is_probed_once_per_process_across_loops(spy):
    # given
    await ensure_driver("default")
    # when
    await open_in_second_loop("default")
    # then
    assert spy.probes == 1


async def test_server_info_matches_the_live_server_version(spy):
    # given
    await ensure_driver("default")
    live = int(await admin_scalar("SHOW server_version_num"))
    # when
    info = registry.server_info("default")
    # then
    assert info == PostgreSQLServerInfo(pg_version=live)


async def test_server_info_is_derived_from_the_driver_capabilities(spy):
    # given
    driver = await ensure_driver("default")
    # when
    info = registry.server_info("default")
    # then
    assert info == PostgreSQLServerInfo(
        pg_version=pg_version_number(driver.capabilities.server_version)
    )


async def test_failed_first_open_leaves_no_entry_and_no_server_info(spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert (outcome.entries_after_failure, outcome.server_info_after_failure) == ([], None)


async def test_failed_first_open_raises_a_django_database_error(spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert isinstance(outcome.error, DjangoDbError)


async def test_open_is_retried_with_the_corrected_config(spy):
    # when
    outcome = await fail_first_open_then_recover("default")
    # then
    assert (
        aliases_of(outcome.entries_after_recovery),
        outcome.server_info_after_recovery is not None,
    ) == (["default"], True)


async def test_configure_again_reflects_the_test_database_name(spy_env):
    # when
    await rename_then_restore(spy_env)
    # then
    assert last_prepared_name(spy_env) == live_database_name()
    assert "renamed_before_test_db" in prepared_names(spy_env)


async def test_configure_after_a_driver_is_open_raises_runtime_error(spy):
    # given
    await ensure_driver("default")
    # when / then
    with pytest.raises(RuntimeError):
        configure()


async def test_close_removes_the_entries_of_the_loop(spy):
    # given
    await setup()
    # when
    await close()
    # then
    assert registry.entries() == []


async def test_open_entry_belongs_to_the_running_loop(spy):
    # given
    await setup()
    # when
    entries = registry.entries()
    # then
    assert entries == [("default", asyncio.get_running_loop())]


async def test_a_compile_wrapper_that_was_never_injected_does_attempt_a_sync_connection(spy):
    # when
    attempts = await attempts_reading_pg_version_without_injection("default")
    # then
    assert attempts == 1


async def test_close_ends_every_session_of_the_pool_on_the_server(spy, app_name):
    # given
    await setup()
    open_before_close = await server_connections(app_name)
    # when
    await close()
    # then
    assert open_before_close >= 1
    assert await server_connections_after_settling(app_name) == 0


async def test_close_has_removed_the_registry_entries_of_the_loop(spy):
    # given
    await setup()
    # when
    await close()
    # then
    assert registry.entries() == []


async def test_the_driver_opened_after_configure_reaches_the_test_database(spy):
    # when
    name = await current_database_of_driver("default")
    # then
    assert name == live_database_name()


async def test_a_rename_after_configure_is_refused_instead_of_reaching_the_old_database(spy):
    # when / then
    with pytest.raises(ImproperlyConfigured, match="changed since configure"):
        await ensure_after_rename("default")


async def test_a_refused_rename_leaves_no_entry(spy):
    # given
    await failing(ensure_after_rename("default"), ImproperlyConfigured)
    # when
    entries = registry.entries()
    # then
    assert entries == []
