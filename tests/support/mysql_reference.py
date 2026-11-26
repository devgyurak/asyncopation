"""Reference values from Django's own sync MySQL backend (mysqlclient), compared with the driver.

mysqlclient is imported only here and through Django's real `DatabaseWrapper`; the library under
test never imports it. Every sync call runs in a worker thread (`sync_to_async`).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async

from tests.support.mysql_env import Row, my_settings, open_my_driver


@dataclass(frozen=True)
class RowsComparison:
    async_rows: list[Row]
    sync_rows: list[Row]


@dataclass(frozen=True)
class ValueComparison:
    async_value: Any
    sync_value: Any


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
    name = f"{type(exc).__module__}.{type(exc).__qualname__}"
    if type(exc).__module__.startswith("django.db") and exc.args and isinstance(exc.args[0], int):
        return f"{name}({exc.args[0]!r})"  # the errno (or message) is part of the outcome
    return name


def real_wrapper(
    options: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None
) -> Any:
    """Django's real MySQL `DatabaseWrapper` (mysqlclient) for the test database."""
    from django.db.backends.mysql.base import DatabaseWrapper

    return DatabaseWrapper(my_settings(options=options, overrides=overrides), alias="default")


# --------------------------------------------------------------------------- sync side
async def sync_rows(
    options: dict[str, Any] | None, sql: str, params: Any = None, *, overrides: Any = None
) -> list[Row]:
    """The rows Django's own sync cursor returns (fresh wrapper, own connection)."""

    def run() -> list[Row]:
        wrapper = real_wrapper(options, overrides)
        try:
            with wrapper.cursor() as cursor:
                cursor.execute(sql, params)
                return [tuple(row) for row in cursor.fetchall()]
        finally:
            wrapper.close()

    return await sync_to_async(run)()


async def sync_connect_error(
    options: dict[str, Any] | None, overrides: dict[str, Any] | None
) -> BaseException | None:
    """The exception Django's `ensure_connection()` raises for these settings (None if it works)."""

    def run() -> BaseException | None:
        wrapper = real_wrapper(options, overrides)
        try:
            wrapper.ensure_connection()
        except Exception as exc:
            return exc
        finally:
            wrapper.close()
        return None

    return await sync_to_async(run)()


async def sync_server_data(options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Django's `mysql_server_data` (the six keys) of a fresh sync wrapper."""

    def run() -> dict[str, Any]:
        wrapper = real_wrapper(options)
        try:
            return dict(wrapper.mysql_server_data)
        finally:
            wrapper.close()

    return await sync_to_async(run)()


async def sync_mysql_version(options: dict[str, Any] | None = None) -> tuple[int, ...]:
    def run() -> tuple[int, ...]:
        wrapper = real_wrapper(options)
        try:
            return tuple(wrapper.mysql_version)
        finally:
            wrapper.close()

    return await sync_to_async(run)()


async def sync_transaction_isolation(options: dict[str, Any] | None = None) -> str:
    rows = await sync_rows(options, "SELECT @@transaction_isolation")
    return rows[0][0]


# --------------------------------------------------------------------------- comparisons
async def compare_with_sync(
    sql: str, params: Any = None, *, options: dict[str, Any] | None = None
) -> RowsComparison:
    """Run `sql` through the async driver and through Django's sync cursor; normalise both."""
    async with (
        open_my_driver(options=options) as driver,
        driver.connection() as conn,
    ):
        async_rows = await conn.fetch_all(sql, params)
    reference = await sync_rows(options, sql, params)
    return RowsComparison(normalise(async_rows), normalise(reference))


async def compare_server_data(options: dict[str, Any] | None = None) -> ValueComparison:
    """(driver `server_data` as a dict, Django's sync `mysql_server_data`)."""
    async with open_my_driver(options=options) as driver:
        ours = dataclasses.asdict(driver.server_data)
    return ValueComparison(ours, await sync_server_data(options))


async def compare_server_version(options: dict[str, Any] | None = None) -> ValueComparison:
    """(driver `capabilities.server_version`, Django's sync `mysql_version`)."""
    async with open_my_driver(options=options) as driver:
        ours = driver.capabilities.server_version
    return ValueComparison(tuple(ours), await sync_mysql_version(options))


async def compare_transaction_isolation(options: dict[str, Any] | None = None) -> ValueComparison:
    """(`@@transaction_isolation` on a pooled connection, on Django's sync connection)."""
    async with (
        open_my_driver(options=options) as driver,
        driver.connection() as conn,
    ):
        ours = (await conn.fetch_all("SELECT @@transaction_isolation", None))[0][0]
    return ValueComparison(ours, await sync_transaction_isolation(options))


@dataclass(frozen=True)
class OutcomePair:
    """Each side is `("rows", normalised rows)` or `("error", "module.ExceptionClass")`."""

    async_outcome: tuple[str, Any]
    sync_outcome: tuple[str, Any]


async def compare_outcomes(
    sql: str, params: Any = None, *, options: dict[str, Any] | None = None
) -> OutcomePair:
    """Run `sql` through the driver and through Django's sync cursor; errors compare by class."""

    async def ours() -> tuple[str, Any]:
        try:
            async with open_my_driver(options=options) as driver, driver.connection() as conn:
                return ("rows", normalise(await conn.fetch_all(sql, params)))
        except Exception as exc:
            return ("error", error_name(exc))

    async def theirs() -> tuple[str, Any]:
        try:
            return ("rows", normalise(await sync_rows(options, sql, params)))
        except Exception as exc:
            return ("error", error_name(exc))

    return OutcomePair(await ours(), await theirs())
