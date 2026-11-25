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


def element_types(lists: list[list[Any]]) -> set[type]:
    """Types of the elements of every nested list (e.g. the strings inside ``citext[]`` values)."""
    return python_types([element for items in lists for element in items])


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
    """Sync Django as it behaves without ``django.contrib.postgres`` (no type handlers).

    Disconnects a process-global signal receiver: it assumes tests run one at a time and that no
    other connection is created inside the block (true for these sequential async tests).
    """
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
    connections_without_hstore: int
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
        connections_without_hstore=sum(not has_loader(c.adapters, oids) for c in instances),
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


async def insert_and_read_back(
    table: str, column: str, value: Any, options: dict[str, Any] | None = None
) -> list[Any]:
    """Insert `value` as a parameter through the driver, read the column back."""
    async with (
        open_driver(options=options, register_contrib_types=True) as driver,
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
    """Run the pool's configure step on a driver that never fetched the OIDs.

    ``open()`` always fetches them first (the probe), so this state is unreachable in production:
    with the flag on the step must refuse (RuntimeError) rather than skip silently, because hstore
    would then read back as text. Returns whether the configured connection has the hstore loader.
    """
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
        return has_loader(raw.adapters, await hstore_oids())
    finally:
        await raw.close()


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


# --------------------------------------------------------------------------- recorder sanity
async def recorded_for_a_known_query() -> list[str]:
    """What the recorder sees for a plain ``pg_type`` lookup (it must not be blind)."""
    sql = "SELECT 1 FROM pg_type WHERE typname = %s"
    with recorded_catalog_queries() as seen:
        async with open_driver(register_contrib_types=False) as driver, driver.connection() as conn:
            await conn.fetch_all(sql, ("hstore",))
    return list(seen)


# --------------------------------------------------------------------------- roles
@dataclass(frozen=True)
class RoleRead:
    value: Any
    catalog_queries: list[str]


async def read_hstore_as_assumed_role(options: dict[str, Any]) -> RoleRead:
    """Open with ``OPTIONS["assume_role"]`` set to a fresh unprivileged role; read one hstore."""
    from tests.support.drivers.admin import temporary_role

    async with temporary_role("asyncopation_hstore_role") as role:
        with recorded_catalog_queries() as seen:
            async with (
                open_driver(
                    options={**options, "assume_role": role}, register_contrib_types=True
                ) as driver,
                driver.connection() as conn,
            ):
                value = (await conn.fetch_all(ONE_HSTORE_SQL, None))[0][0]
    return RoleRead(value, list(seen))


# --------------------------------------------------------------------------- several schemas
SCHEMA_DATABASE = f"asyncopation_schemas_{RUN_ID}"
EXTENSION_SCHEMA = "hstore_ext"
SHADOW_SCHEMA = "hstore_shadow"


async def _query_in(database: str, sql: str) -> None:
    from django.db import connections

    cfg = connections["default"].settings_dict
    async with await psycopg.AsyncConnection.connect(
        dbname=database,
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        host=cfg["HOST"],
        port=cfg["PORT"],
        autocommit=True,
    ) as connection:
        await connection.execute(sql)  # ty: ignore[no-matching-overload]


async def create_multi_schema_database() -> str:
    """hstore installed in a schema that is not on the search_path, and a second type that is
    also called ``hstore`` (a domain) in another schema: two ``pg_type`` rows, two OID pairs."""
    await admin_query(f"DROP DATABASE IF EXISTS {SCHEMA_DATABASE}")
    await admin_query(f"CREATE DATABASE {SCHEMA_DATABASE} TEMPLATE template0")
    await _query_in(SCHEMA_DATABASE, f"CREATE SCHEMA {EXTENSION_SCHEMA}")
    await _query_in(SCHEMA_DATABASE, f"CREATE EXTENSION hstore SCHEMA {EXTENSION_SCHEMA}")
    await _query_in(SCHEMA_DATABASE, f"CREATE SCHEMA {SHADOW_SCHEMA}")
    await _query_in(
        SCHEMA_DATABASE, f"CREATE DOMAIN {SHADOW_SCHEMA}.hstore AS {EXTENSION_SCHEMA}.hstore"
    )
    return SCHEMA_DATABASE


async def drop_multi_schema_database() -> None:
    await admin_query(f"DROP DATABASE IF EXISTS {SCHEMA_DATABASE} WITH (FORCE)")


async def read_from_each_schema(database: str) -> list[Any]:
    """One value cast to the extension's hstore, one to the same-named domain in another schema."""
    queries = [
        f'SELECT \'"a"=>"1"\'::{EXTENSION_SCHEMA}.hstore',
        f"SELECT '\"b\"=>NULL'::{SHADOW_SCHEMA}.hstore",
    ]
    async with (
        open_driver(overrides={"NAME": database}, register_contrib_types=True) as driver,
        driver.connection() as conn,
    ):
        return [(await conn.fetch_all(sql, None))[0][0] for sql in queries]


async def schema_loader_presence(database: str) -> tuple[int, int]:
    """``(OIDs found, connections missing one of their loaders)``: loaders for every ``hstore`` type of ``database`` (both schemas), on every connection.

    ``read_from_each_schema`` cannot show this: PostgreSQL reports a domain's values under its base
    type's OID, so both reads decode through the extension's loader.
    """
    connection_class = recording_connection_class()
    pool = {"min_size": 1, "max_size": 1, "connection_class": connection_class}
    async with (
        open_driver(overrides={"NAME": database}, pool=pool, register_contrib_types=True) as driver,
        driver.connection() as conn,
    ):
        rows = await conn.fetch_all(
            "SELECT oid, typarray FROM pg_type WHERE typname = 'hstore'", None
        )
    oids = tuple(oid for row in rows for oid in row)
    instances = connection_class.instances  # ty: ignore[missing-attribute]
    return len(oids), sum(not has_loader(c.adapters, oids) for c in instances)


# --------------------------------------------------------------------------- real wiring
async def zoo_rows_from_the_registered_driver() -> list[Any]:
    """PgFieldZoo rows through the driver ``asyncopation`` opened for ``default`` (engines wiring)."""
    from asyncopation.connection.lifecycle import ensure_driver

    driver = await ensure_driver("default")
    async with driver.connection() as conn:
        return await conn.fetch_all(pg_field_zoo_sql(), None)


# --------------------------------------------------------------------------- concurrency, txns
async def concurrent_hstore_reads(count: int) -> list[Any]:
    """`count` simultaneous reads, each holding its own pooled connection."""
    import asyncio

    pool = {"min_size": count, "max_size": count}

    async def read(driver: Any) -> Any:
        async with driver.connection() as conn:
            value = (await conn.fetch_all(ONE_HSTORE_SQL, None))[0][0]
            await conn.fetch_all("SELECT pg_sleep(0.1)", None)  # keeps the connection busy
            return value

    async with open_driver(pool=pool, register_contrib_types=True) as driver:
        await settled_stats(driver, min_idle=count)
        return list(await asyncio.gather(*(read(driver) for _ in range(count))))


async def hstore_in_transaction_and_after_rollback(options: dict[str, Any]) -> list[Any]:
    async with (
        open_driver(options=options, register_contrib_types=True) as driver,
        driver.connection() as conn,
    ):
        await conn.begin()
        inside = (await conn.fetch_all(ONE_HSTORE_SQL, None))[0][0]
        await conn.rollback()
        after = (await conn.fetch_all(ONE_HSTORE_SQL, None))[0][0]
    return [inside, after]
