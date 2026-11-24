"""A borrowed psycopg3 connection (``AsyncDbConnection``).

Mirrors Django's sync PostgreSQL backend (6.1): client-side binding by default
(``AsyncClientCursor``; streams use ``ClientCursorMixin`` + ``AsyncServerCursor`` like Django's
``ServerSideCursor``), psycopg's server-side binding with ``OPTIONS["server_side_binding"] =
True``. The choice lives in the cursor classes selected at pool creation and in
``_stream_cursor``; no other code branches on it.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any, NoReturn

import psycopg
from django.db import InterfaceError
from psycopg.client_cursor import ClientCursorMixin
from psycopg.pq import TransactionStatus

from asyncopation.drivers.base import ExecResult, Params
from asyncopation.drivers.errors import raise_mapped
from asyncopation.drivers.postgresql.errors import is_disconnect

type Row = tuple[Any, ...]

_CURSOR_IDS = itertools.count(1)
_RECOVERY_TIMEOUT = 5.0  # seconds to cancel + roll back a statement before discarding


# Ported from django/db/backends/postgresql/base.py::ServerSideCursor @ 6.1.3
# Changes: ``Database.ServerCursor`` -> ``AsyncServerCursor``; Django's ``CursorMixin`` (callproc,
# which we do not expose) is dropped.
class AsyncClientServerCursor(ClientCursorMixin, psycopg.AsyncServerCursor):
    """A named cursor that binds parameters client side (what Django's streams use)."""


class _ReleasedConnection:
    """Stands in for the raw connection once it went back to the pool (ADR-0001 rule 5 spirit)."""

    __slots__ = ()

    def __getattr__(self, name: str) -> NoReturn:
        raise InterfaceError("the connection was returned to the pool and can no longer be used")


_RELEASED: Any = _ReleasedConnection()


class PostgresConnection:
    """One borrowed connection. Errors are translated only around driver I/O (never around the
    caller's code) and a hot-path statement costs one ``try``/``except``, no context managers."""

    __slots__ = ("_raw", "_server_side_binding", "_server_side_cursors")

    def __init__(
        self, raw: psycopg.AsyncConnection, *, server_side_binding: bool, server_side_cursors: bool
    ) -> None:
        self._raw = raw
        self._server_side_binding = server_side_binding
        self._server_side_cursors = server_side_cursors

    # ------------------------------------------------------------------ statements
    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        try:
            async with self._raw.cursor() as cursor:
                await _execute(cursor, sql, params)
                return await cursor.fetchall() if cursor.description else []
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        try:
            async with self._raw.cursor() as cursor:
                await _execute(cursor, sql, params)
                rows = await cursor.fetchall() if cursor.description else None
                return ExecResult(rowcount=cursor.rowcount, rows=rows)
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        try:
            async with self._raw.cursor() as cursor:
                await cursor.executemany(sql, batch)  # ty: ignore[invalid-argument-type]
                return ExecResult(rowcount=cursor.rowcount)
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        cursor = self._stream_cursor()
        try:
            try:
                await _execute(cursor, sql, params)
            except asyncio.CancelledError:
                await self._discard()  # state of the cursor/statement is unknown
                raise
            except Exception as exc:
                await self._fail(exc)
            try:
                yield self._chunks(cursor, chunk)  # the caller's body is not wrapped
            except asyncio.CancelledError:
                # A held (autocommit) cursor survives a rollback and the statement state is
                # unknown: never hand this connection back (ADR-0001 6b).
                await self._discard()
                raise
        finally:
            await self._close_cursor(cursor)

    async def _chunks(self, cursor: Any, chunk: int) -> AsyncIterator[list[Row]]:
        while True:
            try:
                rows = await cursor.fetchmany(chunk)
            except asyncio.CancelledError:
                await self._discard()  # a cancelled FETCH leaves the open cursor in unknown state
                raise
            except Exception as exc:
                await self._fail(exc)
            if not rows:
                return
            yield rows

    def _stream_cursor(self) -> Any:
        if not self._server_side_cursors:  # DISABLE_SERVER_SIDE_CURSORS: chunk a client cursor
            return self._raw.cursor()
        # Unique across wrappers: a pooled connection outlives the wrapper that borrowed it.
        name = f"_asyncopation_curs_{next(_CURSOR_IDS)}"
        # Ported from django/db/backends/postgresql/base.py::create_cursor @ 6.1.3
        # Changes: none in the decision logic (`withhold=connection.autocommit`, scrollable=False);
        # the cursor must survive outside a transaction. Added: DISABLE_SERVER_SIDE_CURSORS falls
        # back to a client cursor (see above) instead of Django's per-queryset check. Not ported:
        # the `register_tzloader` call for a cursor whose connection adapters carry another time
        # zone - our connections always get the adapters template for their own zone.
        withhold = self._raw.autocommit
        if self._server_side_binding:
            return self._raw.cursor(name, scrollable=False, withhold=withhold)
        return AsyncClientServerCursor(self._raw, name=name, scrollable=False, withhold=withhold)

    async def _close_cursor(self, cursor: Any) -> None:
        try:
            await cursor.close()
        except asyncio.CancelledError:
            await self._discard()  # CLOSE may not have run: a held cursor must not reach the pool
            raise
        except psycopg.Error:
            pass  # a failed transaction cannot CLOSE; its end drops the cursor anyway

    # ------------------------------------------------------------------ transactions
    async def begin(self) -> None:
        # Leave autocommit like Django's set_autocommit(False): psycopg issues BEGIN lazily with
        # the configured isolation level.
        try:
            await self._raw.set_autocommit(False)
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def commit(self) -> None:
        try:
            await self._raw.commit()
            await self._raw.set_autocommit(True)
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def rollback(self) -> None:
        if self._raw.closed:  # discarded after a cancel or disconnect: the transaction is gone
            return
        try:
            await self._raw.rollback()
            await self._raw.set_autocommit(True)
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def savepoint(self, sid: str) -> None:
        await self.execute(f"SAVEPOINT {_quote(sid)}", None)

    async def release_savepoint(self, sid: str) -> None:
        await self.execute(f"RELEASE SAVEPOINT {_quote(sid)}", None)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self.execute(f"ROLLBACK TO SAVEPOINT {_quote(sid)}", None)

    # ------------------------------------------------------------------ plumbing
    async def _fail(self, exc: Exception) -> NoReturn:
        """Discard a dead connection, then re-raise ``exc`` as its ``django.db`` class."""
        if isinstance(exc, psycopg.Error) and (is_disconnect(exc) or self._raw.broken):
            await self._discard()
        raise_mapped(exc, psycopg)

    async def _recover_from_cancel(self) -> None:
        """Leave the connection idle or discard it - never hand back an unknown state."""
        raw = self._raw
        try:
            if raw.closed:
                return
            if not raw.autocommit:
                # A cancelled statement inside a transaction: rolling back silently would let the
                # caller carry on in a new implicit transaction. The transaction is lost, so the
                # connection is too (ADR-0001 6c; the transaction layer marks it broken, #5).
                await self._discard()
            elif raw.info.transaction_status != TransactionStatus.IDLE:
                async with asyncio.timeout(_RECOVERY_TIMEOUT):
                    await raw.cancel_safe()
                    await raw.rollback()
                if raw.info.transaction_status != TransactionStatus.IDLE:
                    await self._discard()
        except BaseException:
            await self._discard()

    async def reset(self) -> None:
        """Called when the connection goes back to the pool: end any open transaction.

        psycopg's own exit path would COMMIT whatever is open, so a transaction started with a
        raw ``BEGIN`` (autocommit stays on) must be rolled back here, not only ``begin()`` ones.
        """
        raw = self._raw
        if raw.closed or (raw.autocommit and raw.info.transaction_status == TransactionStatus.IDLE):
            return
        try:
            async with asyncio.timeout(_RECOVERY_TIMEOUT):
                if raw.info.transaction_status != TransactionStatus.IDLE:
                    await raw.rollback()
                if not raw.autocommit:
                    await raw.set_autocommit(True)
        except Exception:
            # The body's outcome (including a CancelledError) must win over a failed cleanup.
            await self._discard()
        except BaseException:
            await self._discard()
            raise

    def release(self) -> None:
        """The connection is back in the pool: any further use raises ``InterfaceError``."""
        self._raw = _RELEASED

    async def _discard(self) -> None:
        # Errors while closing are irrelevant; a CancelledError must still propagate.
        with suppress(Exception):
            await self._raw.close()


async def _execute(cursor: Any, sql: str, params: Params) -> None:
    # Like Django's CursorWrapper: `params is None` means "no parameters" (no `%` processing).
    if params is None:
        await cursor.execute(sql)
    else:
        await cursor.execute(sql, params)


def _quote(sid: str) -> str:
    # Ported from django/db/backends/postgresql/operations.py::quote_name @ 6.1.3 - unchanged.
    return sid if sid.startswith('"') and sid.endswith('"') else f'"{sid}"'
