"""Reference values from Django's own sync PostgreSQL backend, compared with the async driver.

The reference wrapper uses the alias ``default``: with ``django.contrib.postgres`` installed its
``connection_created`` handler looks the alias up in ``connections`` (hstore/citext OIDs).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from asgiref.sync import sync_to_async
from django.test import override_settings
from psycopg.types.json import Jsonb

from tests.support.drivers.admin import (
    UTC_CASE,
    Row,
    TzCase,
    backend_ends,
    open_driver,
    pg_settings,
)


@dataclass(frozen=True)
class RowsComparison:
    async_rows: list[Row]
    sync_rows: list[Row]


@dataclass(frozen=True)
class OutcomeComparison:
    """Each side is `("rows", normalised rows)` or `("error", "module.ExceptionClass")`."""

    async_outcome: tuple[str, Any]
    sync_outcome: tuple[str, Any]


@dataclass(frozen=True)
class ValueComparison:
    async_value: Any
    sync_value: Any


# --------------------------------------------------------------------------- normalisation
def field_zoo_sql(exclude_char: str | None = None) -> str:
    """All FieldZoo rows, optionally without the row whose `char` is `exclude_char`."""
    from harness.catalog.models import FieldZoo

    where = f" WHERE \"char\" <> '{exclude_char}'" if exclude_char else ""
    columns = ", ".join(f'"{f.column}"' for f in FieldZoo._meta.concrete_fields)
    return f"SELECT {columns} FROM {FieldZoo._meta.db_table}{where} ORDER BY id"


def _plain(value: Any) -> str:
    if isinstance(value, memoryview):
        value = bytes(value)
    return repr(value)


def normalise(rows: Iterable[Iterable[Any]]) -> list[Row]:
    """Rows as (type name, repr) pairs so that value *and* type are compared."""
    return [tuple((type(v).__qualname__, _plain(v)) for v in row) for row in rows]


def error_name(exc: BaseException | None) -> str:
    if exc is None:
        return "no error"
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


# --------------------------------------------------------------------------- sync side
async def sync_rows(
    case: TzCase,
    options: dict[str, Any] | None,
    sql: str,
    params: Any = None,
    *,
    transactional: bool = False,
    overrides: dict[str, Any] | None = None,
) -> list[Row]:
    """The rows Django's own sync PostgreSQL cursor returns (fresh wrapper, own connection).

    The wrapper uses alias ``default`` because Django's contrib.postgres handler looks the alias up
    in ``connections`` and caches the hstore/citext OIDs per process from ``connections["default"]``
    (a worker-thread connection that stays open). The OIDs are therefore those of the test
    database: ``overrides={"NAME": ...}`` must not point at a database with other OIDs.
    """

    def run() -> list[Row]:
        from django.db.backends.postgresql.base import DatabaseWrapper

        settings_dict = pg_settings(
            options=options, db_time_zone=case.db_time_zone, overrides=overrides
        )
        from django.db import connections

        # Django caches the hstore/citext OIDs of connections["default"]: another NAME would get
        # the wrong ones (see the docstring).
        assert settings_dict["NAME"] == connections["default"].settings_dict["NAME"]
        with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
            wrapper = DatabaseWrapper(settings_dict, alias="default")
            try:
                if transactional:
                    wrapper.set_autocommit(False)
                with wrapper.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = [tuple(row) for row in cursor.fetchall()]
                if transactional:
                    wrapper.rollback()
                return rows
            finally:
                wrapper.close()

    return await sync_to_async(run)()


async def sync_connect_error(
    case: TzCase, options: dict[str, Any] | None, overrides: dict[str, Any] | None
) -> BaseException | None:
    """The exception Django's `ensure_connection()` raises for these settings (None if it works)."""

    def run() -> BaseException | None:
        from django.db.backends.postgresql.base import DatabaseWrapper

        settings_dict = pg_settings(
            options=options, db_time_zone=case.db_time_zone, overrides=overrides
        )
        with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
            wrapper = DatabaseWrapper(settings_dict, alias="default")
            try:
                wrapper.ensure_connection()
            except Exception as exc:
                return exc
            finally:
                wrapper.close()
            return None

    return await sync_to_async(run)()


async def sync_idle_timeout_error_name() -> str:
    """Class Django's sync backend raises on a session the server ended (idle in transaction)."""
    from django.db.backends.postgresql.base import DatabaseWrapper

    options = {"options": "-c idle_in_transaction_session_timeout=300ms"}
    holder: dict[str, Any] = {}

    def start() -> int:
        wrapper = DatabaseWrapper(pg_settings(options=options), alias="default")
        holder["wrapper"] = wrapper  # created and used in the one sync worker thread
        wrapper.set_autocommit(False)
        with wrapper.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            return cursor.fetchall()[0][0]

    def use_again() -> str:
        try:
            with holder["wrapper"].cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception as exc:
            return error_name(exc)
        return "no error"

    def close() -> None:
        holder["wrapper"].close()

    try:
        pid = await sync_to_async(start)()
        await backend_ends(pid)  # the server terminates the idle session
        return await sync_to_async(use_again)()
    finally:
        await sync_to_async(close)()


# --------------------------------------------------------------------------- comparisons
async def compare_with_sync(
    sql: str,
    params: Any = None,
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
    register_contrib_types: bool | None = None,
) -> RowsComparison:
    """Run `sql` through the async driver and through Django's sync cursor; normalise both."""
    async with (
        open_driver(
            case=case, options=options, register_contrib_types=register_contrib_types
        ) as driver,
        driver.connection() as conn,
    ):
        async_rows = await conn.fetch_all(sql, params)
    reference = await sync_rows(case, options, sql, params)
    return RowsComparison(normalise(async_rows), normalise(reference))


async def compare_outcomes(
    sql: str,
    params: Any = None,
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
) -> OutcomeComparison:
    """Like `compare_with_sync`, but an exception on either side is part of the outcome."""
    try:
        async with open_driver(case=case, options=options) as driver, driver.connection() as conn:
            async_outcome = ("rows", normalise(await conn.fetch_all(sql, params)))
    except Exception as exc:
        async_outcome = ("error", error_name(exc))
    try:
        sync_outcome = ("rows", normalise(await sync_rows(case, options, sql, params)))
    except Exception as exc:
        sync_outcome = ("error", error_name(exc))
    return OutcomeComparison(async_outcome, sync_outcome)


async def compare_session_value(
    case: TzCase, options: dict[str, Any] | None, sql: str, *, transactional: bool = False
) -> ValueComparison:
    """A one-value session query on the async driver and on a sync wrapper with the same OPTIONS."""
    async with open_driver(case=case, options=options) as driver, driver.connection() as conn:
        if transactional:
            await conn.begin()
        async_value = (await conn.fetch_all(sql, None))[0][0]
        if transactional:
            await conn.rollback()
    sync_value = (await sync_rows(case, options, sql, transactional=transactional))[0][0]
    return ValueComparison(async_value, sync_value)


# --------------------------------------------------------------------------- parameter round trips
ROUND_TRIP_SQL = (
    "SELECT %s::numeric, %s::timestamptz, %s::date, %s::time, %s::interval, %s::uuid, "
    "%s::bytea, %s::text, %s::boolean, %s::int"
)
EXTRA_ROUND_TRIP_SQL = "SELECT %s::jsonb, %s::inet, %s::float8, %s::int = ANY(%s)"


def round_trip_params(case: TzCase) -> tuple[Any, ...]:
    moment = datetime(2026, 11, 24, 12, 30, 15, 123456)
    return (
        Decimal("-1234.500000"),
        moment.replace(tzinfo=UTC) if case.use_tz else moment,
        date(2026, 11, 24),
        time(23, 59, 59, 999999),
        timedelta(days=1, microseconds=5),
        uuid.UUID(int=2**100 + 7),
        b"\x00\xff\x10",
        "한글 ✓",
        True,
        None,
    )


def extra_round_trip_params() -> tuple[Any, ...]:
    return (Jsonb({"a": [1, 2.5, None], "b": "x"}), "192.168.0.1", 0.1 + 0.2, 2, [1, 2, 3])


async def compare_round_trip(case: TzCase, options: dict[str, Any] | None) -> RowsComparison:
    return await compare_with_sync(
        ROUND_TRIP_SQL, round_trip_params(case), case=case, options=options
    )


async def compare_extra_round_trip(options: dict[str, Any] | None) -> RowsComparison:
    return await compare_with_sync(EXTRA_ROUND_TRIP_SQL, extra_round_trip_params(), options=options)
