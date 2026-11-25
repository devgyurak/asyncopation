"""Helpers for the real PostgreSQL engine: spies, settings overrides, server checks."""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from unittest import mock

from tests.support.lifecycle.fakes import PG_ENGINE, Prepared, Recorder


def forget_wrapper(alias: str) -> None:
    from asyncopation.compile import postgresql

    postgresql._WRAPPERS.pop(alias, None)


def injected_wrapper(alias: str) -> Any:
    from asyncopation.compile import postgresql

    return postgresql._WRAPPERS.get(alias)


def pg_engine() -> Any:
    from asyncopation.connection.engines import FACTORIES

    return FACTORIES[PG_ENGINE]()


class CountingDriver:
    """Delegates to a real driver and counts open/close (psycopg itself is never patched)."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def open(self) -> None:
        self._recorder.open_attempts += 1
        await self._inner.open()
        self._recorder.opens += 1

    async def close(self) -> None:
        self._recorder.closes += 1
        await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def install_spy_engine(monkeypatch: Any, recorder: Recorder) -> None:
    """Wrap the real PostgreSQL engine so that every prepare/build/open/probe is recorded."""
    from asyncopation.connection import engines

    real = engines.FACTORIES[PG_ENGINE]()

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        recorder.prepares.append(Prepared(alias, settings_dict["NAME"], use_tz, time_zone))
        inner = real.prepare(alias, settings_dict, use_tz=use_tz, time_zone=time_zone)

        def factory() -> CountingDriver:
            driver = CountingDriver(inner(), recorder)
            recorder.built.append(driver)
            return driver

        return factory

    def probe(driver: Any) -> Any:
        recorder.probes += 1
        return real.probe(driver)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> None:
        recorder.injects.append((alias, info))
        real.inject(alias, settings_dict, info)

    spy = dataclasses.replace(real, prepare=prepare, probe=probe, inject=inject)
    monkeypatch.setitem(engines.FACTORIES, PG_ENGINE, lambda: spy)


@contextlib.contextmanager
def patched_alias_settings(alias: str = "default", **overrides: Any) -> Generator[None]:
    """Django's configured settings with `overrides` applied to `alias` (e.g. a wrong PASSWORD)."""
    from django.db import connections

    databases = copy.deepcopy(dict(connections.settings))
    databases[alias].update(overrides)
    with mock.patch.dict(connections.__dict__, {"settings": databases}):
        yield


def live_database_name(alias: str = "default") -> str:
    from django.db import connections

    return connections.settings[alias]["NAME"]


@dataclass
class FailedThenRecovered:
    error: BaseException | None
    server_info_after_failure: Any
    entries_after_failure: list[tuple[str, Any]]
    entries_after_recovery: list[tuple[str, Any]]
    server_info_after_recovery: Any


async def fail_first_open_then_recover(alias: str = "default") -> FailedThenRecovered:
    """Open with a wrong password (fails), then reconfigure with the right one and open again."""
    from asyncopation.connection import configure, registry
    from asyncopation.connection.lifecycle import ensure_driver

    error: BaseException | None = None
    with patched_alias_settings(alias, PASSWORD="definitely-wrong"):
        configure()
        try:
            await ensure_driver(alias)
        except Exception as exc:
            error = exc
    after = (registry.server_info(alias), registry.entries())
    configure()
    await ensure_driver(alias)
    return FailedThenRecovered(
        error, after[0], after[1], registry.entries(), registry.server_info(alias)
    )


async def rename_then_restore(recorder: Recorder) -> None:
    """configure() against a renamed database, then again against the live (test) name."""
    from asyncopation.connection import configure

    with patched_alias_settings("default", NAME="renamed_before_test_db"):
        configure()
    configure()


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
        wrapper.pg_version  # noqa: B018 - Django reads it for version-dependent SQL
    return counter.count


async def attempts_reading_pg_version_without_injection(alias: str) -> int:
    """Positive control for the tripwire: a compile wrapper that was never injected tries to
    connect when its server version is read."""
    from django.db import connections

    from asyncopation.compile.postgresql import PostgreSQLCompileWrapper
    from tests.support.connect_counter import count_sync_connection_attempts

    wrapper = PostgreSQLCompileWrapper(dict(connections.settings[alias]), alias)
    with count_sync_connection_attempts() as counter, contextlib.suppress(BaseException):
        wrapper.pg_version  # noqa: B018 - the read is the attempt
    return counter.count


def new_application_name() -> str:
    return f"asyncopation-test-{uuid.uuid4().hex[:12]}"


@contextlib.contextmanager
def application_name_override(name: str, alias: str = "default") -> Generator[None]:
    """Run with `OPTIONS["application_name"] = name` for `alias` (so its sessions can be counted)."""
    from django.db import connections

    options = {**connections.settings[alias].get("OPTIONS", {}), "application_name": name}
    with patched_alias_settings(alias, OPTIONS=options):
        yield


async def server_connections(name: str) -> int:
    """Sessions on the server whose `application_name` is `name` (pg_stat_activity)."""
    from tests.support.drivers.admin import admin_scalar

    return int(
        await admin_scalar(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s", [name]
        )
    )


async def _none_left(name: str) -> bool:
    return await server_connections(name) == 0


async def server_connections_after_settling(name: str) -> int:
    """`server_connections`, once the server has reaped the closed sessions (polled, bounded)."""
    from tests.support.aio import wait_until

    await wait_until(lambda: _none_left(name), within=10.0)
    return await server_connections(name)


async def current_database_of_driver(alias: str = "default") -> str:
    """`SELECT current_database()` on a connection borrowed from the registry's driver."""
    from asyncopation.connection.lifecycle import ensure_driver

    driver = await ensure_driver(alias)
    async with driver.connection() as connection:
        rows = await connection.fetch_all("SELECT current_database()", None)
    return rows[0][0]


async def ensure_after_rename(alias: str = "default") -> Any:
    """`configure()` against the live name, rename `NAME`, then open: raises (fail closed)."""
    from asyncopation.connection import configure
    from asyncopation.connection.lifecycle import ensure_driver

    configure()
    with patched_alias_settings(alias, NAME="renamed_after_configure"):
        return await ensure_driver(alias)


def alias_value_during_and_after(
    key: str, value: Any, alias: str = "default"
) -> tuple[Any, Any, Any]:
    """`DATABASES[alias][key]` before, inside and after `patched_alias_settings(alias, key=value)`."""
    from django.db import connections

    before = connections.settings[alias].get(key)
    with patched_alias_settings(alias, **{key: value}):
        during = connections.settings[alias].get(key)
    return before, during, connections.settings[alias].get(key)


def other_alias_value_during(key: str, value: Any, alias: str, other: str) -> tuple[Any, Any]:
    """`DATABASES[other][key]` before and inside a patch of `alias`."""
    from django.db import connections

    before = connections.settings[other].get(key)
    with patched_alias_settings(alias, **{key: value}):
        during = connections.settings[other].get(key)
    return before, during
