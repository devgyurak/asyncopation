"""psycopg3 driver: pool lifecycle, per-connection session setup and borrowing (ADR-0003/0004)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from asyncopation.drivers.base import Capabilities, PoolStats
from asyncopation.drivers.postgresql.connection import PostgresConnection
from asyncopation.drivers.postgresql.errors import is_disconnect, translate_errors
from asyncopation.drivers.postgresql.settings import PostgresConfig, build_config


class PostgresDriver:
    """``AsyncDriver`` for PostgreSQL. Construction does no I/O and creates no asyncio objects."""

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._pool: AsyncConnectionPool | None = None
        self._capabilities: Capabilities | None = None

    @classmethod
    def from_settings(
        cls, alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str
    ) -> PostgresDriver:
        """Build from a ``DATABASES`` entry plus ``settings.USE_TZ`` / ``settings.TIME_ZONE``."""
        return cls(build_config(alias, settings_dict, use_tz=use_tz, time_zone=time_zone))

    @property
    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            raise RuntimeError("PostgresDriver is not open: capabilities are known after open()")
        return self._capabilities

    async def open(self) -> None:
        """Open the pool and read the server version. Callers serialize ``open()`` (ADR-0004)."""
        if self._pool is not None:
            return
        config = self._config
        pool = AsyncConnectionPool(
            "",
            kwargs=config.conn_kwargs,
            configure=self._configure,
            name=config.alias,
            open=False,
            **config.pool_kwargs,
        )
        try:
            with translate_errors():
                await pool.open(wait=True)
                async with pool.connection() as raw:
                    server_version = divmod(raw.info.server_version, 10000)
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        self._capabilities = Capabilities(
            server_version=server_version,
            server_side_cursors=config.server_side_cursors,
            atomic_execute_many=True,  # executemany runs in one pipeline sync: all or nothing
        )

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[PostgresConnection]:
        pool = self._pool
        if pool is None:
            raise RuntimeError("PostgresDriver is not open")
        with translate_errors():  # PoolTimeout -> OperationalError (ADR-0001 Decision 8)
            async with pool.connection() as raw:
                connection = PostgresConnection(
                    raw,
                    server_side_binding=self._config.server_side_binding,
                    server_side_cursors=self._config.server_side_cursors,
                )
                try:
                    yield connection
                finally:
                    await connection.reset()

    def is_disconnect(self, exc: BaseException) -> bool:
        return is_disconnect(exc)

    def pool_stats(self) -> PoolStats:
        """Read-only pool counters (zeros when closed)."""
        if self._pool is None:
            return PoolStats(0, 0, 0)
        stats = self._pool.get_stats()
        size, available = stats["pool_size"], stats["pool_available"]
        return PoolStats(in_use=size - available, idle=available, waiting=stats["requests_waiting"])

    async def _configure(self, raw: psycopg.AsyncConnection) -> None:
        """Per physical connection; port of Django's ``get_new_connection`` + ``_configure_*``."""
        # Ported from django/db/backends/postgresql/base.py::_configure_timezone,
        # _configure_role and get_new_connection @ 6.1.3
        # Changes: awaited calls; the connection is in autocommit mode so nothing is committed.
        config = self._config
        if config.isolation_level is not None:
            await raw.set_isolation_level(config.isolation_level)
        if config.timezone_name and config.timezone_name != raw.info.parameter_status("TimeZone"):
            await raw.execute("SELECT set_config('TimeZone', %s, false)", [config.timezone_name])
        if config.assume_role:
            sql = psycopg.AsyncClientCursor(raw).mogrify("SET ROLE %s", [config.assume_role])
            await raw.execute(sql)  # ty: ignore[no-matching-overload]
