"""Helpers for the real MySQL/MariaDB engine: the injected wrapper, server threads, failures."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from tests.support.lifecycle.postgres import patched_alias_settings

MY_ENGINE = "django.db.backends.mysql"


def forget_wrapper(alias: str) -> None:
    from asyncopation.compile import mysql

    mysql._WRAPPERS.pop(alias, None)


def injected_wrapper(alias: str) -> Any:
    from asyncopation.compile import mysql

    return mysql._WRAPPERS.get(alias)


def compile_attempts_with_injected_wrapper(alias: str) -> int:
    """Sync connection attempts while compiling a query with the wrapper `setup()` injected."""
    from asyncopation.compile.scope import compile_scope
    from harness.catalog.models import Book
    from tests.support.connect_counter import count_sync_connection_attempts

    wrapper = injected_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        compile_scope(alias, wrapper, autocommit=True),
    ):
        Book.objects.filter(title="book-1").query.sql_with_params()
        wrapper.mysql_version  # noqa: B018 - Django reads it for version-dependent SQL
        wrapper.features.supports_transactions  # noqa: B018 - and the storage engine
    return counter.count


def registry_server_fields(alias: str = "default") -> dict[str, Any]:
    """The server info the registry holds for `alias`, as a plain dict."""
    import dataclasses

    from asyncopation.connection import registry

    info: Any = registry.server_info(alias)
    return dataclasses.asdict(info)


async def registry_and_sync_server_data(options: dict[str, Any] | None = None) -> Any:
    """(registry server info as a dict, Django's sync `mysql_server_data`) for the live alias."""
    from asyncopation.connection.lifecycle import ensure_driver
    from tests.support.mysql_reference import ValueComparison, sync_server_data

    await ensure_driver("default")
    return ValueComparison(registry_server_fields(), await sync_server_data(options))


async def registry_and_sync_server_data_with_init_command(command: str) -> Any:
    """The same, with `OPTIONS["init_command"] = command` applied to both sides."""
    from django.db import connections

    from asyncopation.connection import configure

    options = {**connections.settings["default"].get("OPTIONS", {}), "init_command": command}
    with patched_alias_settings("default", OPTIONS=options):
        configure()
        return await registry_and_sync_server_data({"init_command": command})


@dataclass
class FailedThenRecovered:
    error: BaseException | None
    server_info_after_failure: Any
    entries_after_failure: list[tuple[str, Any]]
    entries_after_recovery: list[tuple[str, Any]]
    server_info_after_recovery: Any


async def fail_first_open_then_recover(alias: str = "default") -> FailedThenRecovered:
    """Open against an unknown database (fails), then reconfigure with the right one."""
    from asyncopation.connection import configure, registry
    from asyncopation.connection.lifecycle import ensure_driver

    error: BaseException | None = None
    with patched_alias_settings(alias, NAME="no_such_database_xyz"):
        configure()
        with contextlib.suppress(Exception):
            await ensure_driver(alias)
        error = await _open_error(alias)
    after = (registry.server_info(alias), registry.entries())
    configure()
    await ensure_driver(alias)
    return FailedThenRecovered(
        error, after[0], after[1], registry.entries(), registry.server_info(alias)
    )


async def _open_error(alias: str) -> BaseException | None:
    from asyncopation.connection.lifecycle import ensure_driver

    try:
        await ensure_driver(alias)
    except Exception as exc:
        return exc
    return None


async def pool_threads_during_setup_and_after_close() -> tuple[int, int]:
    """(live pool threads after `setup()`, live pool threads settled after `close()`): only the
    connections this run's pool opened are counted."""
    from asyncopation.connection import close, setup
    from tests.support.mysql_env import (
        live_thread_count,
        live_threads_settle_to,
        tracked_pool_threads,
    )

    with tracked_pool_threads() as opened:
        await setup()
        during = await live_thread_count(opened)
        await close()
        return during, await live_threads_settle_to(opened, 0)


def pool_stats_of(driver: Any) -> Any:
    return driver.pool_stats()
