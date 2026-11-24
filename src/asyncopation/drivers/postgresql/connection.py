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
from psycopg.pq import ExecStatus, TransactionStatus

from asyncopation.drivers.base import ExecResult, Params
from asyncopation.drivers.errors import raise_mapped
from asyncopation.drivers.postgresql.errors import is_disconnect

type Row = tuple[Any, ...]

_IDLE = TransactionStatus.IDLE
_INERROR = TransactionStatus.INERROR
_TUPLE_STATUSES = frozenset(
    {ExecStatus.TUPLES_OK, ExecStatus.SINGLE_TUPLE, ExecStatus.TUPLES_CHUNK}
)
_CURSOR_IDS = itertools.count(1)
_RECOVERY_TIMEOUT = 5.0  # seconds to cancel + roll back a statement before discarding


# Ported from django/db/backends/postgresql/base.py::ServerSideCursor @ 6.1.3
# Changes: ``Database.ServerCursor`` -> ``AsyncServerCursor``; Django's ``CursorMixin`` (callproc,
# which we do not expose) is dropped.
class AsyncClientServerCursor(ClientCursorMixin, psycopg.AsyncServerCursor):
    """A named cursor that binds parameters client side (what Django's streams use)."""


_RELEASED_MESSAGE = "the connection was returned to the pool and can no longer be used"


class _ReleasedConnection:
    """Stands in for the raw connection once it went back to the pool (ADR-0001 rule 5 spirit)."""

    __slots__ = ()

    def __getattr__(self, name: str) -> NoReturn:
        raise InterfaceError(_RELEASED_MESSAGE)


_RELEASED: Any = _ReleasedConnection()


class PostgresConnection:
    """One borrowed connection. Errors are translated only around driver I/O (never around the
    caller's code) and a hot-path statement costs one ``try``/``except``, no context managers."""

    __slots__ = ("_open_cursors", "_raw", "_server_side_binding", "_server_side_cursors")

    def __init__(
        self, raw: psycopg.AsyncConnection, *, server_side_binding: bool, server_side_cursors: bool
    ) -> None:
        self._raw = raw
        self._open_cursors: list[Any] = []  # stream cursors not closed yet (normally one)
        self._server_side_binding = server_side_binding
        self._server_side_cursors = server_side_cursors

    # ------------------------------------------------------------------ statements
    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        try:
            async with self._raw.cursor() as cursor:
                await _execute(cursor, sql, params)
                return await cursor.fetchall() if _has_result(cursor) else []
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            await self._fail(exc)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        try:
            async with self._raw.cursor() as cursor:
                await _execute(cursor, sql, params)
                rows = await cursor.fetchall() if _has_result(cursor) else None
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
        cursor = None
        try:
            try:
                cursor = self._stream_cursor()
                self._open_cursors.append(cursor)
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
            if cursor is not None:
                await self._close_cursor(cursor)

    async def _chunks(self, cursor: Any, chunk: int) -> AsyncIterator[list[Row]]:
        while True:
            if self._raw is _RELEASED:  # a stale iterator must not touch the next borrower
                raise InterfaceError(_RELEASED_MESSAGE)
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
        if self._raw is _RELEASED:  # reset() already closed it; never touch the next borrower
            return
        if self._raw.pgconn.transaction_status == _INERROR:
            # psycopg silently skips the CLOSE in a failed transaction and still marks the cursor
            # closed. A held (autocommit) cursor survives the rollback and needs the real CLOSE
            # later: keep it tracked, reset() closes it after the rollback. An ordinary cursor
            # dies with the transaction; marking it closed now is all that is left to do.
            if not getattr(cursor, "withhold", False):
                await cursor.close()
                self._forget(cursor)
            return
        try:
            await cursor.close()
        except asyncio.CancelledError:
            await self._discard()  # CLOSE may not have run: a held cursor must not reach the pool
            raise
        except psycopg.Error:
            return  # stays tracked: reset() retries after the rollback
        self._forget(cursor)

    def _forget(self, cursor: Any) -> None:
        with suppress(ValueError):
            self._open_cursors.remove(cursor)

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
        except asyncio.CancelledError:
            # Not _leave_transaction: a cancelled COMMIT leaves the outcome unknown.
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            with suppress(Exception):  # the original error is the one to report
                await self._leave_transaction()  # a failed COMMIT still ended the transaction
            await self._fail(exc)
        await self._leave_transaction()

    async def rollback(self) -> None:
        if self._raw.closed:  # discarded after a cancel or disconnect: the transaction is gone
            return
        try:
            await self._raw.rollback()
        except asyncio.CancelledError:
            await self._recover_from_cancel()
            raise
        except Exception as exc:
            with suppress(Exception):
                await self._leave_transaction()
            await self._fail(exc)
        await self._leave_transaction()

    async def savepoint(self, sid: str) -> None:
        await self.execute(f"SAVEPOINT {_quote(sid)}", None)

    async def release_savepoint(self, sid: str) -> None:
        await self.execute(f"RELEASE SAVEPOINT {_quote(sid)}", None)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self.execute(f"ROLLBACK TO SAVEPOINT {_quote(sid)}", None)

    # ------------------------------------------------------------------ plumbing
    async def _leave_transaction(self) -> None:
        """Back to autocommit once the server has ended the transaction.

        A COMMIT that fails (e.g. a deferred constraint) still ends the transaction: the server
        rolls back and is IDLE again, so the caller's next statement must autocommit as usual.
        """
        raw = self._raw
        if not raw.closed and not raw.autocommit and raw.pgconn.transaction_status == _IDLE:
            await raw.set_autocommit(True)

    async def _fail(self, exc: Exception) -> NoReturn:
        """Discard a dead connection, then re-raise ``exc`` as its ``django.db`` class."""
        if isinstance(exc, psycopg.Error) and (is_disconnect(exc) or self._raw.broken):
            await self._discard()
        raise_mapped(exc, psycopg)

    async def _recover_from_cancel(self) -> None:
        """Leave the connection idle or discard it - never hand back an unknown state.

        Worst case this waits ``_RECOVERY_TIMEOUT`` (5 s: cancel request + draining the aborted
        statement + ROLLBACK) before discarding; a second cancellation during recovery discards at
        once and propagates.
        """
        raw = self._raw
        try:
            if raw.closed:
                return
            if not raw.autocommit:
                # A cancelled statement inside a transaction: rolling back silently would let the
                # caller carry on in a new implicit transaction. The transaction is lost, so the
                # connection is too (ADR-0001 6c; the transaction layer marks it broken, #5).
                await self._discard()
            elif raw.pgconn.transaction_status != _IDLE:
                async with asyncio.timeout(_RECOVERY_TIMEOUT):
                    await raw.cancel_safe()
                    await raw.rollback()
                if raw.pgconn.transaction_status != _IDLE:
                    await self._discard()
        except Exception:
            await self._discard()
        except BaseException:
            await self._discard()
            raise

    async def reset(self) -> None:
        """Called when the connection goes back to the pool: end any open transaction and stream.

        psycopg's own exit path would COMMIT whatever is open, so a transaction started with a
        raw ``BEGIN`` (autocommit stays on) must be rolled back here, not only ``begin()`` ones. A
        stream still open (its cursor is ``WITH HOLD`` in autocommit) is CLOSEd; if that fails the
        connection is discarded.
        """
        raw = self._raw
        cursors, self._open_cursors = self._open_cursors, []
        if raw.closed or (
            not cursors and raw.autocommit and raw.pgconn.transaction_status == _IDLE
        ):
            return
        try:
            async with asyncio.timeout(_RECOVERY_TIMEOUT):
                if raw.pgconn.transaction_status != _IDLE:
                    await raw.rollback()  # drops ordinary server cursors, not held ones
                if not raw.autocommit:
                    await raw.set_autocommit(True)
                for cursor in cursors:
                    try:
                        await cursor.close()
                    except psycopg.errors.InvalidCursorName:
                        # A held cursor declared inside a raw BEGIN died with the rollback: only
                        # the client object is left to mark closed (psycopg's base-class close).
                        await super(psycopg.AsyncServerCursor, cursor).close()
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


def _has_result(cursor: Any) -> bool:
    """``cursor.description is not None`` without building the ``Column`` list (hot path)."""
    result = cursor.pgresult
    # Same test as psycopg's `description`: columns, or a tuples result with zero columns
    # (`SELECT`), which Django's sync cursor returns as `[()]`.
    return result is not None and bool(result.nfields or result.status in _TUPLE_STATUSES)
