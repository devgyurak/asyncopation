"""Vocabulary, settings, driver factories and the admin connection for driver tests.

The PostgreSQL driver is imported lazily so that modules which only need the vocabulary (unit
tests) import without it.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from django.db import connections

from asyncopation.drivers.base import PoolStats
from harness.settings import VENDOR
from tests.support.aio import wait_until

POSTGRES_ONLY = pytest.mark.skipif(VENDOR != "postgresql", reason="PostgreSQL driver tests")

SCRATCH_TABLE = "asyncopation_scratch"
SCRATCH_DDL = (
    "id serial PRIMARY KEY, name text NOT NULL UNIQUE, "
    "qty integer NOT NULL DEFAULT 0 CHECK (qty >= 0)"
)
SLOW_COMMIT_FUNCTION = "asyncopation_slow_commit"

type Row = tuple[Any, ...]


# --------------------------------------------------------------------------- vocabulary
@dataclass(frozen=True)
class TzCase:
    """One cell of the USE_TZ / TIME_ZONE matrix (ADR-0003 validation)."""

    use_tz: bool
    db_time_zone: str | None  # DATABASES[alias]["TIME_ZONE"]
    settings_time_zone: str  # settings.TIME_ZONE

    @property
    def time_zone(self) -> str:
        """The connection time zone as Django's `DatabaseWrapper.timezone_name` computes it."""
        if not self.use_tz:
            return self.settings_time_zone
        return self.db_time_zone or "UTC"


UTC_CASE = TzCase(True, None, "UTC")


@dataclass(frozen=True)
class Binding:
    """`server_side_binding` OPTIONS variant."""

    options: dict[str, Any]
    server_side: bool


# --------------------------------------------------------------------------- settings, drivers
def pg_settings(
    *,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    db_time_zone: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Django DATABASES entry for the test database, with OPTIONS merged."""
    settings_dict = copy.deepcopy(dict(connections["default"].settings_dict))
    merged = {**settings_dict.get("OPTIONS", {}), **(options or {})}
    if pool is not None:
        merged["pool"] = pool
    settings_dict["OPTIONS"] = merged
    settings_dict["TIME_ZONE"] = db_time_zone
    settings_dict.update(overrides or {})
    return settings_dict


def build_driver(
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Any:
    """A `PostgresDriver` that is not opened yet."""
    from asyncopation.drivers.postgresql import PostgresDriver

    settings_dict = pg_settings(
        options=options, pool=pool, db_time_zone=case.db_time_zone, overrides=overrides
    )
    return PostgresDriver.from_settings(
        "default", settings_dict, use_tz=case.use_tz, time_zone=case.settings_time_zone
    )


@asynccontextmanager
async def open_driver(
    *,
    case: TzCase = UTC_CASE,
    options: dict[str, Any] | None = None,
    pool: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> AsyncGenerator[Any]:
    driver = build_driver(case=case, options=options, pool=pool, overrides=overrides)
    await driver.open()
    try:
        yield driver
    finally:
        await driver.close()


def login_overrides(user: str, password: str) -> dict[str, Any]:
    return {"USER": user, "PASSWORD": password}


# --------------------------------------------------------------------------- admin connection
async def admin_query(sql: str, params: Sequence[Any] | None = None) -> list[Row]:
    """Run one statement on a separate autocommit connection (rows, or [] without a result)."""
    cfg = connections["default"].settings_dict
    async with (
        await psycopg.AsyncConnection.connect(
            dbname=cfg["NAME"],
            user=cfg["USER"],
            password=cfg["PASSWORD"],
            host=cfg["HOST"],
            port=cfg["PORT"],
            options="-c lock_timeout=5s",
            autocommit=True,
        ) as admin,
        admin.cursor() as cursor,
    ):
        await cursor.execute(sql, params)  # ty: ignore[invalid-argument-type]
        if cursor.description is None:
            return []
        return [tuple(row) for row in await cursor.fetchall()]


async def admin_scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    return (await admin_query(sql, params))[0][0]


async def create_scratch_table() -> str:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
    await admin_query(f"CREATE TABLE {SCRATCH_TABLE} ({SCRATCH_DDL})")
    return SCRATCH_TABLE


async def drop_scratch_table() -> None:
    await admin_query(f"DROP TABLE IF EXISTS {SCRATCH_TABLE}")
    await admin_query(f"DROP FUNCTION IF EXISTS {SLOW_COMMIT_FUNCTION}()")


async def install_slow_commit(table: str) -> None:
    """A deferred trigger that makes `COMMIT` of any insert into `table` sleep for a while."""
    await admin_query(
        f"CREATE FUNCTION {SLOW_COMMIT_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN PERFORM pg_sleep(10); RETURN NULL; END $$"
    )
    await admin_query(
        f"CREATE CONSTRAINT TRIGGER slow_commit AFTER INSERT ON {table} "
        f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {SLOW_COMMIT_FUNCTION}()"
    )


async def scratch_count(table: str = SCRATCH_TABLE) -> int:
    return await admin_scalar(f"SELECT count(*) FROM {table}")


async def scratch_names(table: str = SCRATCH_TABLE) -> list[str]:
    return [row[0] for row in await admin_query(f"SELECT name FROM {table} ORDER BY id")]


@asynccontextmanager
async def temporary_role(name: str) -> AsyncGenerator[str]:
    await admin_query(f"DROP ROLE IF EXISTS {name}")
    await admin_query(f"CREATE ROLE {name}")
    try:
        yield name
    finally:
        await admin_query(f"DROP ROLE IF EXISTS {name}")


@asynccontextmanager
async def temporary_login_role(name: str, password: str) -> AsyncGenerator[str]:
    await admin_query(f"DROP ROLE IF EXISTS {name}")
    await admin_query(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'")
    try:
        yield name
    finally:
        await admin_query(f"DROP ROLE IF EXISTS {name}")


async def set_role_password(name: str, password: str) -> None:
    await admin_query(f"ALTER ROLE {name} PASSWORD '{password}'")


async def set_role_login(name: str, *, login: bool) -> None:
    await admin_query(f"ALTER ROLE {name} {'LOGIN' if login else 'NOLOGIN'}")


async def server_version_tuple() -> tuple[int, int]:
    number = int(await admin_scalar("SHOW server_version_num"))
    major, minor = divmod(number, 10000)
    return major, minor


# --------------------------------------------------------------------------- backends and pools
async def backend_exists(pid: int) -> bool:
    return bool(await admin_scalar("SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,)))


async def backend_ends(pid: int, *, within: float = 5.0) -> bool:
    """True once the backend is gone, False when it is still there after `within` seconds."""

    async def gone() -> bool:
        return not await backend_exists(pid)

    try:
        await wait_until(gone, within=within)
    except TimeoutError:
        return False
    return True


async def terminate_backend(pid: int) -> bool:
    await admin_query("SELECT pg_terminate_backend(%s)", (pid,))
    return await backend_ends(pid)


async def settled_stats(driver: Any, *, min_idle: int = 0, within: float = 5.0) -> PoolStats:
    """Pool counters once nothing is in use or waiting and `min_idle` connections are idle.

    Returns the current counters when that does not happen within `within` seconds.
    """

    async def idle() -> bool:
        stats = driver.pool_stats()
        return stats.in_use == 0 and stats.waiting == 0 and stats.idle >= min_idle

    with suppress(TimeoutError):
        await wait_until(idle, within=within)
    return driver.pool_stats()


@dataclass(frozen=True)
class Timed[T]:
    value: T | None
    error: BaseException | None
    elapsed: float


async def timed(coro: Any, *, limit: float = 8.0) -> Timed[Any]:
    """Await `coro` with a safety limit; capture its value or exception and the elapsed time."""
    started = time.monotonic()
    try:
        async with asyncio.timeout(limit):
            value = await coro
    except Exception as exc:
        return Timed(None, exc, time.monotonic() - started)
    return Timed(value, None, time.monotonic() - started)
