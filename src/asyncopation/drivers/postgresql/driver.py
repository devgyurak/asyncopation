"""psycopg3 driver: pool lifecycle, per-connection session setup and borrowing (ADR-0003/0004)."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from asyncopation.drivers.base import Capabilities, PoolStats
from asyncopation.drivers.errors import raise_mapped
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
        """Build from a ``DATABASES`` entry plus ``settings.USE_TZ`` / ``settings.TIME_ZONE``.

        Call it from the configure phase (``AppConfig.ready()``, ADR-0004), never from inside the
        running event loop: resolving a time zone name reads the tz database from disk
        (``zoneinfo``), which is blocking I/O.
        """
        return cls(build_config(alias, settings_dict, use_tz=use_tz, time_zone=time_zone))

    @property
    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            raise RuntimeError("PostgresDriver is not open: capabilities are known after open()")
        return self._capabilities

    async def open(self) -> None:
        """Open the pool. Callers serialize ``open()`` (ADR-0004).

        A first, direct connection (configured like every pooled one) makes bad credentials, an
        unknown database or an invalid ``assume_role`` fail at once with the same ``django.db``
        exception Django's sync backend raises - psycopg_pool would only retry in the background
        and report a ``PoolTimeout`` after its timeout. It also yields the server version without
        a query. Nothing is left open when this raises.
        """
        if self._pool is not None:
            return
        config = self._config
        # Constructing the pool first validates its sizes (psycopg_pool raises ``ValueError`` for
        # min_size > max_size) before any connection is made; nothing runs until it is opened.
        pool = AsyncConnectionPool(
            "",
            kwargs=config.conn_kwargs,
            configure=self._configure,
            name=config.alias,
            open=False,
            **config.pool_kwargs,
        )
        server_version = await self._probe()
        try:
            with translate_errors():
                await pool.open(wait=True)
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        self._capabilities = Capabilities(
            server_version=server_version,
            server_side_cursors=config.server_side_cursors,
            # psycopg runs executemany in one pipeline sync (all or nothing); without pipeline
            # support it falls back to one statement per parameter set.
            atomic_execute_many=psycopg.AsyncPipeline.is_supported(),
        )

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        self._capabilities = None  # known only while open
        if pool is not None:
            await pool.close()

    def connection(self) -> _Borrow:
        """Borrow a connection (``async with``); it goes back to the pool on exit."""
        pool = self._pool
        if pool is None:
            raise RuntimeError("PostgresDriver is not open")
        return _Borrow(pool, self._config)

    def is_disconnect(self, exc: BaseException) -> bool:
        return is_disconnect(exc)

    def pool_stats(self) -> PoolStats:
        """Read-only pool counters (zeros when closed)."""
        if self._pool is None:
            return PoolStats(0, 0, 0)
        stats = self._pool.get_stats()
        size, available = stats["pool_size"], stats["pool_available"]
        return PoolStats(in_use=size - available, idle=available, waiting=stats["requests_waiting"])

    async def _probe(self) -> tuple[int, ...]:
        with translate_errors():
            connection_class = (
                self._config.pool_kwargs.get("connection_class") or psycopg.AsyncConnection
            )
            raw = await connection_class.connect(**self._config.conn_kwargs)
            try:
                await self._configure(raw)
                return divmod(raw.info.server_version, 10000)
            finally:
                await raw.close()

    async def _configure(self, raw: psycopg.AsyncConnection) -> None:
        """Per physical connection; port of Django's ``get_new_connection`` + ``_configure_*``."""
        # Ported from django/db/backends/postgresql/base.py::_configure_timezone,
        # _configure_role and get_new_connection, and operations.py::compose_sql @ 6.1.3
        # Changes: awaited calls; the connection is in autocommit mode so nothing is committed.
        config = self._config
        if config.isolation_level is not None:
            await raw.set_isolation_level(config.isolation_level)
        if config.timezone_name and config.timezone_name != raw.info.parameter_status("TimeZone"):
            await raw.execute("SELECT set_config('TimeZone', %s, false)", [config.timezone_name])
        if config.assume_role:
            sql = psycopg.AsyncClientCursor(raw).mogrify("SET ROLE %s", [config.assume_role])
            await raw.execute(sql)  # ty: ignore[no-matching-overload]


class _Borrow:
    """``async with driver.connection()``: a class (not a generator context manager) because this
    is the hot path. Uses ``getconn``/``putconn`` so psycopg's commit-on-exit never runs."""

    __slots__ = ("_config", "_connection", "_pool", "_raw")

    def __init__(self, pool: AsyncConnectionPool, config: PostgresConfig) -> None:
        self._pool = pool
        self._config = config

    async def __aenter__(self) -> PostgresConnection:
        try:
            raw = await self._pool.getconn()
        except Exception as exc:  # PoolTimeout -> OperationalError (ADR-0001 Decision 8)
            raise_mapped(exc, psycopg)
        self._raw = raw
        self._connection = PostgresConnection(
            raw,
            server_side_binding=self._config.server_side_binding,
            server_side_cursors=self._config.server_side_cursors,
        )
        return self._connection

    async def __aexit__(self, *exc_info: object) -> None:
        connection = self._connection
        try:
            await connection.reset()
        finally:
            connection.release()
            await self._pool.putconn(self._raw)
