"""asyncmy driver: pool lifecycle, per-connection session setup and borrowing (ADR-0003/0004)."""

from __future__ import annotations

import asyncio
from typing import Any, NoReturn

import asyncmy
from django.db import OperationalError

from asyncopation._django.mysql import MYSQL_SERVER_DATA_SQL
from asyncopation.drivers.base import Capabilities, PoolStats
from asyncopation.drivers.mysql.connection import MySQLConnection
from asyncopation.drivers.mysql.errors import is_disconnect, translate_errors
from asyncopation.drivers.mysql.server_data import MySQLServerData
from asyncopation.drivers.mysql.settings import MySQLConfig, build_config

_CLOSE_TIMEOUT = 5.0  # seconds a close waits for borrowed connections before terminating them


class MySQLDriver:
    """``AsyncDriver`` for MySQL and MariaDB. Construction does no I/O and creates no asyncio
    objects (asyncmy's pool binds to the running loop when it is built, in ``open()``)."""

    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._pool: Any = None
        self._capabilities: Capabilities | None = None
        self._server_data: MySQLServerData | None = None
        self._waiting = 0

    @classmethod
    def from_settings(
        cls, alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str
    ) -> MySQLDriver:
        """Build from a ``DATABASES`` entry plus ``settings.USE_TZ`` / ``settings.TIME_ZONE``.

        Call it from the configure phase, never from inside the running event loop.
        """
        return cls(build_config(alias, settings_dict, use_tz=use_tz, time_zone=time_zone))

    @property
    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            raise RuntimeError("MySQLDriver is not open: capabilities are known after open()")
        return self._capabilities

    @property
    def server_data(self) -> MySQLServerData:
        if self._server_data is None:
            raise RuntimeError("MySQLDriver is not open: server data is known after open()")
        return self._server_data

    async def open(self) -> None:
        """Open the pool. Callers serialize ``open()`` (ADR-0004).

        A first, direct connection - configured like a pooled one apart from the
        ``SET SQL_AUTO_IS_NULL`` assignment, which depends on the result, so the user's
        ``init_command`` and the isolation level apply - makes bad credentials or an unknown
        database fail at once with Django's exception. It runs Django's ``mysql_server_data``
        ``SELECT`` once; the result (frozen) and ``Capabilities`` come from it, and the pool's
        ``init_command`` depends on it (ADR-0003 Decision 2). A failing probe
        leaves nothing open; asyncmy gives no handle on a pool whose initial connections fail
        part way (``min_size`` > 1), so those sockets wait for garbage collection.
        """
        if self._pool is not None:
            return
        config = self._config
        data = await self._probe()
        server_version = data.server_version  # parsed before any pool exists: it can raise
        with translate_errors():
            pool = await asyncmy.create_pool(
                **config.pool_kwargs, **config.pooled_kwargs(sql_auto_is_null=data.sql_auto_is_null)
            )
        self._pool = pool
        self._server_data = data
        self._capabilities = Capabilities(
            server_version=server_version,
            server_side_cursors=config.server_side_cursors,
            # The driver rewrites INSERT / REPLACE ... VALUES into multi-row statements of at most
            # 64 KiB and runs every other statement once per parameter set; a server error leaves
            # the earlier statements applied, so a batch is not atomic.
            atomic_execute_many=False,
        )

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        self._capabilities = None  # known only while open
        self._server_data = None
        if pool is None:
            return
        pool.close()
        try:
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await pool.wait_closed()  # waits for every borrowed connection
        except TimeoutError:
            pool.terminate()
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await pool.wait_closed()

    def connection(self) -> _Borrow:
        """Borrow a connection (``async with``); it goes back to the pool on exit."""
        if self._pool is None:
            raise RuntimeError("MySQLDriver is not open")
        return _Borrow(self)

    def is_disconnect(self, exc: BaseException) -> bool:
        return is_disconnect(exc)

    def pool_stats(self) -> PoolStats:
        """Read-only pool counters (zeros when closed). ``waiting`` counts acquires in progress."""
        pool = self._pool
        if pool is None:
            return PoolStats(0, 0, 0)
        return PoolStats(
            in_use=pool.size - pool.freesize, idle=pool.freesize, waiting=self._waiting
        )

    async def _probe(self) -> MySQLServerData:
        """First, direct connection: runs Django's ``mysql_server_data`` ``SELECT`` once."""
        with translate_errors():
            raw = await asyncmy.connect(**self._config.conn_kwargs)
            try:
                async with raw.cursor() as cursor:
                    await cursor.execute(MYSQL_SERVER_DATA_SQL)
                    row = await cursor.fetchone()
            finally:
                await raw.ensure_closed()
            return MySQLServerData.from_row(row)


class _Borrow:
    """``async with driver.connection()``: a class (not a generator context manager) because this
    is the hot path."""

    __slots__ = ("_connection", "_driver", "_pool", "_raw")

    def __init__(self, driver: MySQLDriver) -> None:
        self._driver = driver

    async def __aenter__(self) -> MySQLConnection:
        driver = self._driver
        config = driver._config
        pool = driver._pool
        if pool is None:
            raise RuntimeError("MySQLDriver is not open")
        if config.max_waiting and driver._waiting >= config.max_waiting:
            raise OperationalError(
                f"too many acquires waiting for a connection of {config.alias!r} "
                f"(max_waiting={config.max_waiting})"
            )
        driver._waiting += 1
        deadline = asyncio.timeout(config.acquire_timeout)
        try:
            # One `asyncio.timeout` block, no task of its own: asyncmy's `_acquire` has no await
            # between taking a connection and returning it, so a timeout or cancel can never land
            # after a connection was checked out (ADR-0001 Decision 8).
            async with deadline:
                raw = await self._acquire(pool, config.health_checks)
        except TimeoutError as exc:
            if deadline.expired():
                raise OperationalError(
                    f"timed out after {config.acquire_timeout}s waiting for a connection of "
                    f"{config.alias!r}"
                ) from exc
            raise
        except Exception as exc:
            _raise_acquire_error(exc, config.alias)
        finally:
            driver._waiting -= 1
        self._raw = raw
        self._pool = pool
        self._connection = MySQLConnection(raw, server_side_cursors=config.server_side_cursors)
        return self._connection

    @staticmethod
    async def _acquire(pool: Any, health_checks: bool) -> Any:
        while True:
            raw = await pool.acquire()
            if not health_checks:
                return raw
            try:
                await raw.ping(reconnect=False)
            except BaseException as exc:
                await _drop(pool, raw)
                if isinstance(exc, asyncmy.errors.Error):
                    continue  # dead idle connection: try another
                raise
            return raw

    async def __aexit__(self, *exc_info: object) -> None:
        connection = self._connection
        raw = self._raw
        try:
            await connection.reset()
        finally:
            connection.release()
            await self._pool.release(raw)


def _raise_acquire_error(exc: Exception, alias: str) -> NoReturn:
    if isinstance(exc, RuntimeError) and "closing pool" in str(exc):
        # asyncmy: the pool is closing (a close() raced this acquire); psycopg_pool's `PoolClosed`
        # is an OperationalError too.
        raise OperationalError(f"the connection pool of {alias!r} is closed") from exc
    with translate_errors():
        raise exc
    raise exc  # unreachable: translate_errors re-raises  # pragma: no cover


async def _drop(pool: Any, raw: Any) -> None:
    """Close a connection that failed its health check and hand it back so the pool forgets it."""
    try:
        await raw.ensure_closed()
    finally:
        await pool.release(raw)
