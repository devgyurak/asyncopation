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
from typing import Any

import psycopg
from psycopg.client_cursor import ClientCursorMixin
from psycopg.pq import TransactionStatus

from asyncopation.drivers.base import ExecResult, Params
from asyncopation.drivers.postgresql.errors import is_disconnect, translate_errors

type Row = tuple[Any, ...]

_CURSOR_IDS = itertools.count(1)
_RECOVERY_TIMEOUT = 5.0  # seconds to cancel + roll back a statement before discarding


# Ported from django/db/backends/postgresql/base.py::ServerSideCursor @ 6.1.3
# Changes: ``Database.ServerCursor`` -> ``AsyncServerCursor``; Django's ``CursorMixin`` (callproc,
# which we do not expose) is dropped.
class AsyncClientServerCursor(ClientCursorMixin, psycopg.AsyncServerCursor):
    """A named cursor that binds parameters client side (what Django's streams use)."""


class PostgresConnection:
    def __init__(
        self, raw: psycopg.AsyncConnection, *, server_side_binding: bool, server_side_cursors: bool
    ) -> None:
        self._raw = raw
        self._server_side_binding = server_side_binding
        self._server_side_cursors = server_side_cursors

    # ------------------------------------------------------------------ statements
    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        async with self._statement(), self._raw.cursor() as cursor:
            await _execute(cursor, sql, params)
            return await cursor.fetchall() if cursor.description else []

    async def execute(self, sql: str, params: Params) -> ExecResult:
        async with self._statement(), self._raw.cursor() as cursor:
            await _execute(cursor, sql, params)
            rows = await cursor.fetchall() if cursor.description else None
            return ExecResult(rowcount=cursor.rowcount, rows=rows)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        async with self._statement(), self._raw.cursor() as cursor:
            await cursor.executemany(sql, batch)  # ty: ignore[invalid-argument-type]
            return ExecResult(rowcount=cursor.rowcount)

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        async with self._statement():
            cursor = self._stream_cursor()
            try:
                await _execute(cursor, sql, params)
                yield self._chunks(cursor, chunk)
            except asyncio.CancelledError:
                # A held (autocommit) cursor survives a rollback and the statement state is
                # unknown: never hand this connection back (ADR-0001 6b).
                await self._discard()
                raise
            finally:
                await self._close_cursor(cursor)

    async def _chunks(self, cursor: Any, chunk: int) -> AsyncIterator[list[Row]]:
        while True:
            async with self._statement():
                rows = await cursor.fetchmany(chunk)
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
        # back to a client cursor (see above) instead of Django's per-queryset check.
        withhold = self._raw.autocommit
        if self._server_side_binding:
            return self._raw.cursor(name, scrollable=False, withhold=withhold)
        return AsyncClientServerCursor(self._raw, name=name, scrollable=False, withhold=withhold)

    async def _close_cursor(self, cursor: Any) -> None:
        # A failed transaction cannot CLOSE; the transaction end drops the cursor anyway.
        with suppress(psycopg.Error):
            await cursor.close()

    # ------------------------------------------------------------------ transactions
    async def begin(self) -> None:
        # Leave autocommit like Django's set_autocommit(False): psycopg issues BEGIN lazily with
        # the configured isolation level.
        async with self._statement():
            await self._raw.set_autocommit(False)

    async def commit(self) -> None:
        async with self._statement():
            await self._raw.commit()
            await self._raw.set_autocommit(True)

    async def rollback(self) -> None:
        if self._raw.closed:  # discarded after a cancel or disconnect: the transaction is gone
            return
        async with self._statement():
            await self._raw.rollback()
            await self._raw.set_autocommit(True)

    async def savepoint(self, sid: str) -> None:
        await self.execute(f"SAVEPOINT {_quote(sid)}", None)

    async def release_savepoint(self, sid: str) -> None:
        await self.execute(f"RELEASE SAVEPOINT {_quote(sid)}", None)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self.execute(f"ROLLBACK TO SAVEPOINT {_quote(sid)}", None)

    # ------------------------------------------------------------------ plumbing
    @asynccontextmanager
    async def _statement(self) -> AsyncGenerator[None]:
        """Translate errors, discard dead connections, recover from cancellation (ADR-0001 6b)."""
        try:
            with translate_errors():
                yield
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            if is_disconnect(exc) or self._raw.broken:
                await self._discard()
            raise

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
        """Called when the connection goes back to the pool: end any open transaction."""
        raw = self._raw
        if raw.closed:
            return
        try:
            if not raw.autocommit:
                async with asyncio.timeout(_RECOVERY_TIMEOUT):
                    await raw.rollback()
                    await raw.set_autocommit(True)
        except Exception:
            # The body's outcome (including a CancelledError) must win over a failed cleanup.
            await self._discard()
        except BaseException:
            await self._discard()
            raise

    async def _discard(self) -> None:
        with suppress(BaseException):
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
