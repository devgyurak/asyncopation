"""Support for the hstore/citext registration tests (#43): reads, recorders and probes.

Everything with a loop, a branch or a ``try`` lives here; the test modules only compose it.
"""

from __future__ import annotations

import datetime
from collections.abc import Generator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from typing import Any
from unittest import mock

import psycopg
from psycopg import pq

from tests.support.drivers.admin import (
    RUN_ID,
    admin_query,
    open_driver,
    pg_settings,
    settled_stats,
    terminate_backend,
)
from tests.support.drivers.probes import backend_pid
from tests.support.drivers.reference import RowsComparison, compare_with_sync

PG_FIELD_ZOO_COLUMNS = ("hstore", "nullable_hstore", "hstore_list", "citext", "citext_list")
HSTORE_TABLE = f"asyncopation_hstore_{RUN_ID}"
BARE_DATABASE = f"asyncopation_bare_{RUN_ID}"
ONE_HSTORE_SQL = 'SELECT \'"a"=>"1"\'::hstore'


# --------------------------------------------------------------------------- reads
def python_types(values: list[Any]) -> set[type]:
    return {type(value) for value in values}


def pg_field_zoo_sql(*columns: str) -> str:
    """Selected columns (default: all five) of every PgFieldZoo row, in seed order."""
    from harness.pgcatalog.models import PgFieldZoo

    names = ", ".join(f'"{name}"' for name in (columns or PG_FIELD_ZOO_COLUMNS))
    return f"SELECT {names} FROM {PgFieldZoo._meta.db_table} ORDER BY id"


async def async_column(column: str, options: dict[str, Any] | None = None) -> list[Any]:
    """One PgFieldZoo column as the async driver returns it (contrib types registered)."""
    sql = pg_field_zoo_sql(column)
    async with (
        open_driver(options=options, register_contrib_types=True) as driver,
        driver.connection() as conn,
    ):
        rows = await conn.fetch_all(sql, None)
    return [row[0] for row in rows]


async def compare_zoo_with_sync(options: dict[str, Any] | None = None) -> RowsComparison:
    """All PgFieldZoo columns: async driver (types registered) vs Django's sync cursor."""
    return await compare_with_sync(pg_field_zoo_sql(), options=options, register_contrib_types=True)


@contextmanager
def contrib_handlers_disconnected() -> Generator[None]:
    """Sync Django as it behaves without ``django.contrib.postgres`` (no type handlers)."""
    from django.contrib.postgres.signals import register_type_handlers
    from django.db.backends.signals import connection_created

    connection_created.disconnect(register_type_handlers)
    try:
        yield
    finally:
        connection_created.connect(register_type_handlers)


async def compare_zoo_without_contrib(options: dict[str, Any] | None = None) -> RowsComparison:
    """All PgFieldZoo columns with the flag off on the async side, handlers off on the sync side."""
    with contrib_handlers_disconnected():
        return await compare_with_sync(
            pg_field_zoo_sql(), options=options, register_contrib_types=False
        )


# --------------------------------------------------------------------------- catalog queries
@contextmanager
def recorded_catalog_queries() -> Generator[list[str]]:
    """Names (the ``typname`` parameter) of every ``pg_type`` lookup executed on a psycopg cursor.

    Patches ``AsyncCursor.execute``, which ``AsyncConnection.execute`` and the client-side cursor
    both go through, so it sees the lookup whichever way the driver sends it.
    """
    seen: list[str] = []
    original = psycopg.AsyncCursor.execute

    async def execute(self: Any, query: Any, params: Any = None, *args: Any, **kwargs: Any) -> Any:
        text = query if isinstance(query, str) else repr(query)
        if "pg_type" in text:
            seen.append(str(params[0]) if params else text)
        return await original(self, query, params, *args, **kwargs)

    with mock.patch.object(psycopg.AsyncCursor, "execute", execute):
        yield seen


async def catalog_queries_of_one_open(*, pool: dict[str, Any], register: bool = True) -> list[str]:
    with recorded_catalog_queries() as seen:
        async with open_driver(pool=pool, register_contrib_types=register) as driver:
            await settled_stats(driver, min_idle=pool["min_size"])
    return list(seen)


async def catalog_queries_after_growing_the_pool(connections: int) -> list[str]:
    """Open with one connection, then hold `connections` at once so the pool opens more."""
    pool = {"min_size": 1, "max_size": connections}
    with recorded_catalog_queries() as seen:
        async with (
            open_driver(pool=pool, register_contrib_types=True) as driver,
            AsyncExitStack() as stack,
        ):
            for _ in range(connections):
                await stack.enter_async_context(driver.connection())
    return list(seen)


async def catalog_queries_across_close_and_open() -> list[str]:
    """Open, close, open again on the same driver object."""
    from tests.support.drivers.admin import build_driver

    driver = build_driver(pool={"min_size": 1, "max_size": 1}, register_contrib_types=True)
    with recorded_catalog_queries() as seen:
        await driver.open()
        await driver.close()
        await driver.open()
        await driver.close()
    return list(seen)


# --------------------------------------------------------------------------- reconnect
@dataclass(frozen=True)
class Reconnect:
    old_pid: int
    new_pid: int
    value: Any
    catalog_queries: list[str]


async def reconnect_after_backend_termination() -> Reconnect:
    """Kill the only pooled backend; the health-checked pool replaces it with a new connection."""
    pool = {"min_size": 1, "max_size": 1}
    with recorded_catalog_queries() as seen:
        async with open_driver(
            pool=pool,
            overrides={"CONN_HEALTH_CHECKS": True},
            register_contrib_types=True,
        ) as driver:
            async with driver.connection() as conn:
                old_pid = await backend_pid(conn)
            await terminate_backend(old_pid)
            async with driver.connection() as conn:
                new_pid = await backend_pid(conn)
                value = (await conn.fetch_all(ONE_HSTORE_SQL, None))[0][0]
    return Reconnect(old_pid, new_pid, value, list(seen))


# --------------------------------------------------------------------------- loader presence
def recording_connection_class() -> type[psycopg.AsyncConnection]:
    """A connection class that remembers every instance it creates (probe and pool alike)."""

    class RecordingConnection(psycopg.AsyncConnection):
        instances: list[psycopg.AsyncConnection] = []  # noqa: RUF012 - per-class registry

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            type(self).instances.append(self)

    RecordingConnection.instances = []
    return RecordingConnection


async def hstore_oids() -> tuple[int, ...]:
    """Every hstore type and array type OID in the test database."""
    rows = await admin_query("SELECT oid, typarray FROM pg_type WHERE typname = 'hstore'")
    return tuple(oid for row in rows for oid in row)


def has_loader(adapters: Any, oids: tuple[int, ...]) -> bool:
    return all(adapters.get_loader(oid, pq.Format.TEXT) is not None for oid in oids)


def lacks_loader(adapters: Any, oids: tuple[int, ...]) -> bool:
    return all(adapters.get_loader(oid, pq.Format.TEXT) is None for oid in oids)


@dataclass(frozen=True)
class LoaderPresence:
    connections_seen: int
    every_connection_has_hstore: bool
    template_lacks_hstore: bool


async def loader_presence() -> LoaderPresence:
    """Which hstore loaders the driver's physical connections and the shared template hold."""
    from django.db.backends.postgresql.psycopg_any import get_adapters_template  # ty: ignore[import-not-found]

    connection_class = recording_connection_class()
    oids = await hstore_oids()
    pool = {"min_size": 2, "max_size": 2, "connection_class": connection_class}
    async with open_driver(pool=pool, register_contrib_types=True) as driver:
        await settled_stats(driver, min_idle=2)
    template = get_adapters_template(True, datetime.UTC)
    instances = connection_class.instances  # ty: ignore[missing-attribute]
    return LoaderPresence(
        connections_seen=len(instances),
        every_connection_has_hstore=all(has_loader(c.adapters, oids) for c in instances),
        template_lacks_hstore=lacks_loader(template, oids),
    )


# --------------------------------------------------------------------------- scratch objects
async def create_hstore_table() -> str:
    await admin_query(f"DROP TABLE IF EXISTS {HSTORE_TABLE}")
    await admin_query(
        f"CREATE TABLE {HSTORE_TABLE} (id serial PRIMARY KEY, data hstore, tags hstore[])"
    )
    return HSTORE_TABLE


async def drop_hstore_table() -> None:
    await admin_query(f"DROP TABLE IF EXISTS {HSTORE_TABLE}")


async def insert_and_read_back(table: str, column: str, value: Any) -> list[Any]:
    """Insert `value` as a parameter through the driver, read the column back."""
    async with (
        open_driver(register_contrib_types=True) as driver,
        driver.connection() as conn,
    ):
        await conn.execute(f"INSERT INTO {table} ({column}) VALUES (%s)", (value,))
        rows = await conn.fetch_all(f"SELECT {column} FROM {table} ORDER BY id", None)
    return [row[0] for row in rows]


async def create_bare_database() -> str:
    """A database cloned from template0: neither hstore nor citext is installed."""
    await admin_query(f"DROP DATABASE IF EXISTS {BARE_DATABASE}")
    await admin_query(f"CREATE DATABASE {BARE_DATABASE} TEMPLATE template0")
    return BARE_DATABASE


async def drop_bare_database() -> None:
    await admin_query(f"DROP DATABASE IF EXISTS {BARE_DATABASE} WITH (FORCE)")


@dataclass(frozen=True)
class BareOpen:
    catalog_queries: list[str]
    rows: list[Any]


async def open_and_query_in_database_without_extensions(name: str) -> BareOpen:
    with recorded_catalog_queries() as seen:
        async with (
            open_driver(overrides={"NAME": name}, register_contrib_types=True) as driver,
            driver.connection() as conn,
        ):
            rows = await conn.fetch_all("SELECT 1, 'x'::text", None)
    return BareOpen(list(seen), rows)


# --------------------------------------------------------------------------- configure step
async def configure_unopened_driver(*, register: bool) -> bool:
    """Run the per-connection configure step on a driver that never fetched the OIDs."""
    from asyncopation.drivers.postgresql import PostgresDriver
    from asyncopation.drivers.postgresql.settings import build_config

    config = build_config(
        "default",
        pg_settings(),
        use_tz=True,
        time_zone="UTC",
        register_contrib_types=register,
    )
    driver = PostgresDriver(config)
    raw = await psycopg.AsyncConnection.connect(**config.conn_kwargs)
    try:
        await driver._configure_pooled(raw, [])  # the pool's step, with no OIDs fetched
    finally:
        await raw.close()
    return True


# --------------------------------------------------------------------------- unit helpers
def built_flag(**kwargs: Any) -> bool:
    """``register_contrib_types`` of ``build_config`` for literal offline settings."""
    from asyncopation.drivers.postgresql.settings import build_config
    from tests.support.drivers.drift import drift_settings

    return build_config(
        "default", drift_settings(), use_tz=True, time_zone="UTC", **kwargs
    ).register_contrib_types


def from_settings_flag(**kwargs: Any) -> bool:
    """The flag of a ``PostgresDriver.from_settings(...)`` driver (never opened)."""
    from asyncopation.drivers.postgresql import PostgresDriver
    from tests.support.drivers.drift import drift_settings

    driver = PostgresDriver.from_settings(
        "default", drift_settings(), use_tz=True, time_zone="UTC", **kwargs
    )
    return driver._config.register_contrib_types


def engine_prepared_flag(*, installed: bool) -> bool:
    """The flag ``engines`` hands to ``build_config`` when ``contrib.postgres`` is (not) installed."""
    from django.apps import apps

    from asyncopation.drivers.postgresql import settings as driver_settings
    from tests.support.compile_wrappers import PG_SETTINGS
    from tests.support.lifecycle.postgres import pg_engine

    with (
        mock.patch.object(apps, "is_installed", return_value=installed),
        mock.patch.object(
            driver_settings, "build_config", wraps=driver_settings.build_config
        ) as spy,
    ):
        pg_engine().prepare("default", dict(PG_SETTINGS), use_tz=True, time_zone="UTC")
    return spy.call_args.kwargs["register_contrib_types"]
