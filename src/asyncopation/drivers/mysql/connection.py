"""A borrowed asyncmy connection (``AsyncDbConnection``).

The connection is in autocommit mode while borrowed. A transaction is ``BEGIN`` with autocommit
left on; when the server ends it by itself the session switches to ``SET autocommit = 0`` until
``commit()`` / ``rollback()`` / release switch it back (ADR-0003 Decision 5). A failed ``COMMIT``,
``ROLLBACK`` or autocommit restore discards the connection. asyncmy closes a connection whose
statement or fetch is cancelled, so every cancel path only has to notice that the connection is
gone (ADR-0003 Decision 7).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any, NoReturn
from uuid import UUID

import asyncmy
from asyncmy.constants.SERVER_STATUS import SERVER_STATUS_NO_BACKSLASH_ESCAPES
from asyncmy.cursors import SSCursor
from django.db import InterfaceError, ProgrammingError

from asyncopation.drivers.base import ExecResult, Params
from asyncopation.drivers.mysql.errors import is_disconnect, translate_errors
from asyncopation.drivers.mysql.settings import SqlLiteral

type Row = tuple[Any, ...]

_RECOVERY_TIMEOUT = 5.0  # seconds to drain / roll back before the connection is discarded

_RELEASED_MESSAGE = "the connection was returned to the pool and can no longer be used"


class _ReleasedConnection:
    """Stands in for the raw connection once it went back to the pool (ADR-0001 rule 5 spirit)."""

    __slots__ = ()

    def __getattr__(self, name: str) -> NoReturn:
        raise InterfaceError(_RELEASED_MESSAGE)


_RELEASED: Any = _ReleasedConnection()


class MySQLConnection:
    """One borrowed connection. Errors are translated only around driver I/O."""

    __slots__ = (
        "_autocommit_off",
        "_in_transaction",
        "_open_cursors",
        "_raw",
        "_server_side_cursors",
        "_status_stale",
    )

    def __init__(self, raw: asyncmy.Connection, *, server_side_cursors: bool) -> None:
        self._raw: Any = raw  # asyncmy's stubs type rowcount/lastrowid as `object`
        self._open_cursors: list[Any] = []  # stream cursors not closed yet (normally one)
        self._server_side_cursors = server_side_cursors
        self._in_transaction = False  # begin() was called and not yet ended by commit/rollback
        # an error reply carries no status flags: see _resume_transaction
        self._status_stale = False
        self._autocommit_off = False  # `SET autocommit = 0` after a server-ended transaction

    # ------------------------------------------------------------------ statements
    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        self._check_no_stream()
        cursor = self._raw.cursor()
        query = _format(self._raw, cursor, sql, params)  # client side errors: connection untouched
        await self._resume_transaction()
        try:
            async with cursor:
                await cursor.execute(query)
                return list(await cursor.fetchall()) if cursor.description else []
        except Exception as exc:
            await self._fail(exc)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        self._check_no_stream()
        cursor = self._raw.cursor()
        query = _format(self._raw, cursor, sql, params)
        await self._resume_transaction()
        try:
            async with cursor:
                await cursor.execute(query)
                if cursor.description:  # a result set (MariaDB ``RETURNING``): no lastrowid
                    return ExecResult(rowcount=cursor.rowcount, rows=list(await cursor.fetchall()))
                return ExecResult(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)
        except Exception as exc:
            await self._fail(exc)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        """Run ``sql`` once per parameter set; an ``INSERT`` / ``REPLACE ... VALUES (...)`` is
        rewritten into multi-row statements (ADR-0003 Decision 5, amendment of 2026-11-26).

        The driver owns the batch: every set is prepared, charset-checked, formatted and
        encoded before the first byte is sent, so a client side error (a bad set or key, a NaN, a lone
        surrogate) applies nothing and does no round trip. A server error on a later statement
        leaves the earlier ones applied, as with mysqlclient. A ``RETURNING`` result is not
        collected.
        """
        self._check_no_stream()
        statements = _plan_batch(self._raw, sql, batch)
        if not statements:
            return ExecResult(rowcount=0)
        await self._resume_transaction()
        try:
            async with self._raw.cursor() as cursor:
                rowcount = 0
                for statement in statements:
                    await cursor.execute(statement)
                    rowcount += cursor.rowcount
                return ExecResult(rowcount=rowcount)
        except Exception as exc:
            await self._fail(exc)

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        self._check_no_stream()
        unbuffered = self._server_side_cursors
        cursor = self._raw.cursor(SSCursor if unbuffered else None)
        query = _format(self._raw, cursor, sql, params)
        await self._resume_transaction()
        if unbuffered:
            self._open_cursors.append(cursor)  # a buffered cursor holds nothing on the wire
        failed = False
        abandoned = cancelled = False
        try:
            try:
                await cursor.execute(query)
            except Exception as exc:
                await self._fail(exc)
            try:
                yield self._chunks(cursor, chunk)  # the caller's body is not wrapped
            except BaseException as exc:
                abandoned = True  # the rest of the result set is not wanted
                cancelled = isinstance(exc, asyncio.CancelledError)
                raise
        except BaseException:
            failed = True  # an error from the statement or the body wins over a failing close
            raise
        finally:
            await self._close_cursor(cursor, quiet=failed, abandoned=abandoned, cancelled=cancelled)

    async def _chunks(self, cursor: Any, chunk: int) -> AsyncIterator[list[Row]]:
        while True:
            if self._raw is _RELEASED:  # a stale iterator must not touch the next borrower
                raise InterfaceError(_RELEASED_MESSAGE)
            try:
                rows = await cursor.fetchmany(chunk)
            except Exception as exc:
                await self._fail(exc)
            if not rows:
                return
            yield list(rows)

    async def _close_cursor(
        self, cursor: Any, *, quiet: bool, abandoned: bool, cancelled: bool
    ) -> None:
        """Close a stream cursor; an unbuffered one drains its unread rows first.

        The drain is bounded: past ``_RECOVERY_TIMEOUT`` (or on a failure) the connection is
        discarded, and so it is at once when the stream's body was cancelled or failed outside a
        transaction (``abandoned``: draining a large result for a consumer that is gone only
        holds the connection). A body that failed inside a transaction drains, so a caller that
        handles the error (a rolled back savepoint) keeps its transaction. A cancelled statement
        or fetch already closed the connection (asyncmy).
        """
        raw = self._raw
        if raw is _RELEASED or not raw.connected:
            self._forget(cursor)
            return
        if (
            abandoned
            and cursor in self._open_cursors  # a buffered cursor has nothing left on the wire
            and (cancelled or not (self._in_transaction or raw.get_transaction_status()))
        ):
            self._forget(cursor)
            await self._discard()
            return
        try:
            async with asyncio.timeout(_RECOVERY_TIMEOUT):
                await cursor.close()
        except TimeoutError:
            await self._discard()  # the rest of the result set is still on the wire
        except Exception as exc:
            if not quiet:
                self._forget(cursor)
                await self._fail(exc)
            await self._discard()
        except BaseException:
            self._forget(cursor)
            await self._discard()
            raise
        self._forget(cursor)

    def _forget(self, cursor: Any) -> None:
        with suppress(ValueError):
            self._open_cursors.remove(cursor)

    # ------------------------------------------------------------------ transactions
    async def begin(self) -> None:
        # Autocommit stays on: BEGIN opens the transaction, COMMIT / ROLLBACK end it.
        self._check_no_stream()
        self._status_stale = False
        try:
            await self._raw.begin()
        except Exception as exc:
            await self._fail(exc)
        self._in_transaction = True

    async def commit(self) -> None:
        self._check_no_stream()
        self._in_transaction = False  # whatever the outcome, no transaction is left to resume
        self._status_stale = False
        try:
            await self._raw.commit()
            await self._restore_autocommit()
        except Exception as exc:
            # A failed COMMIT / SET autocommit leaves the session in an unknown transaction
            # state (a retry would commit implicitly): never reuse the connection.
            await self._fail(exc, discard=True)

    async def rollback(self) -> None:
        self._in_transaction = False
        self._status_stale = False
        if not self._raw.connected:  # discarded after a cancel or disconnect: nothing to undo
            return
        try:
            await self._raw.rollback()
            await self._restore_autocommit()
        except Exception as exc:
            await self._fail(exc, discard=True)

    async def savepoint(self, sid: str) -> None:
        await self.execute(f"SAVEPOINT {_quote(sid)}", None)

    async def release_savepoint(self, sid: str) -> None:
        await self.execute(f"RELEASE SAVEPOINT {_quote(sid)}", None)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self.execute(f"ROLLBACK TO SAVEPOINT {_quote(sid)}", None)

    # ------------------------------------------------------------------ plumbing
    async def _resume_transaction(self) -> None:
        """Keep Django's ``autocommit = False`` semantics inside ``begin()``.

        The connection stays in autocommit mode and ``BEGIN`` opens the transaction, so when the
        server ends it by itself (a deadlock rollback, ``innodb_rollback_on_timeout``, a DDL
        statement's implicit commit) the following statements would silently autocommit and a
        later ``rollback()`` could not undo them. Django's sync backend runs with
        ``autocommit = 0`` and starts a new implicit transaction in that case; so does this, by
        switching the session to ``SET autocommit = 0`` (not a second ``BEGIN``, which would also
        release the locks of ``LOCK TABLES``) until ``commit()`` / ``rollback()`` / release switch
        it back. The status flag comes with every server reply, so no extra round trip reads it.
        """
        raw = self._raw
        if not self._in_transaction or self._autocommit_off:
            return  # not in a transaction, or the server starts every transaction itself
        try:
            if self._status_stale:
                # An error reply (a deadlock rolls the transaction back) does not update the
                # status flags asyncmy keeps; one cheap statement refreshes them.
                self._status_stale = False
                await raw.query("DO 0")
            if not raw.get_transaction_status():
                await raw.autocommit(False)
                self._autocommit_off = True
        except Exception as exc:
            await self._fail(exc)

    async def _restore_autocommit(self) -> None:
        if self._autocommit_off:
            await self._raw.autocommit(True)
            self._autocommit_off = False  # a failure discards the connection (see commit())

    def _check_no_stream(self) -> None:
        """One statement or stream in flight (ADR-0001): a statement run while an unbuffered
        stream is open would make asyncmy drain and drop the stream's remaining rows silently."""
        if self._open_cursors:
            raise InterfaceError(
                "a stream is still open on this connection; finish it before running another "
                "statement (or set DISABLE_SERVER_SIDE_CURSORS)"
            )

    async def _fail(self, exc: Exception, *, discard: bool = False) -> NoReturn:
        """Discard a connection that may be unusable, then re-raise ``exc`` as its ``django.db``
        class. Only an error the server reported leaves the protocol in a known state: anything
        else (a decoder's ``ValueError`` while a result set is being read, a malformed packet) may
        leave unread packets behind, and the next borrower would fail with an unrelated error."""
        self._status_stale = self._in_transaction  # the server may have ended the transaction
        if (
            discard
            or not self._raw.connected
            or not isinstance(exc, asyncmy.errors.Error)
            or isinstance(exc, asyncmy.errors.InternalError)
            or is_disconnect(exc)
        ):
            await self._discard()
        with translate_errors():
            raise exc
        raise exc  # unreachable: translate_errors re-raises  # pragma: no cover

    async def reset(self) -> None:
        """Called when the connection goes back to the pool: end any open transaction and stream.

        asyncmy's pool drops a connection released inside a transaction, which would cost a
        reconnect for a plain ``BEGIN`` left open, so it is rolled back here first. A stream still
        open is closed (draining its rows); if that fails the connection is discarded.
        """
        raw = self._raw
        cursors, self._open_cursors = self._open_cursors, []
        self._in_transaction = False
        self._status_stale = False
        if not raw.connected:
            return
        try:
            async with asyncio.timeout(_RECOVERY_TIMEOUT):
                for cursor in cursors:
                    await cursor.close()
                if raw.get_transaction_status():
                    await raw.rollback()
                await self._restore_autocommit()
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
            await self._raw.ensure_closed()


_BINARY_TYPES = (bytes, bytearray, memoryview)
_SET_TYPES = (set, frozenset)
_SEQUENCE_TYPES = (tuple, list, *_SET_TYPES)
_NO_BACKSLASH_ESCAPES = SERVER_STATUS_NO_BACKSLASH_ESCAPES
# The exact types asyncmy's encoders know (it looks encoders up by `type(value)`, so a subclass
# such as an `IntegerChoices` member is not found and would be escaped as a string).
_EXACT_LITERAL_TYPES = frozenset(
    {str, int, bool, float, Decimal, date, datetime, time, timedelta, UUID, type(None)}
)


def _prepare_params(params: Params, raw: Any) -> Params:
    """Make parameters safe for asyncmy's escaping; ``params`` is a tuple, list or dict.

    asyncmy's escaping does not know every value Django passes: a ``list`` inside a parameter
    (``IN %s``) and a ``bytearray`` / ``memoryview`` fail, so they become a ``tuple`` / ``bytes``;
    a ``set`` / ``frozenset`` of strings becomes one comma-joined string and any other set raises
    ``TypeError`` (as mysqlclient); a subclass of ``int``, ``float`` or ``Decimal`` (an
    ``IntegerChoices`` member) becomes its text, which is quoted, as mysqlclient does. Only the
    exact types ``tuple``, ``list``, ``set`` and ``frozenset`` are containers (mysqlclient tests
    ``type(o) is ...``): a subclass (a namedtuple) is sent as its quoted ``str()``. While the
    session has ``sql_mode`` ``NO_BACKSLASH_ESCAPES`` asyncmy's backslash escaping of ``bytes``,
    of strings inside a sequence and of types it has no encoder for would end the string literal
    early (SQL injection): those values are rendered here as literals (``SqlLiteral``; ``X'..'``
    for binary values, the connection's own escaping, which follows the mode, for strings).
    Otherwise asyncmy's escaping stays for ``bytes`` (a hex literal doubles the wire size, so a
    blob close to ``max_allowed_packet`` would stop fitting). A container without such values is
    returned as is.
    """
    hex_mode = bool(raw.server_status & _NO_BACKSLASH_ESCAPES)
    if isinstance(params, dict):
        for key in params:
            if isinstance(key, str) and not key.isascii():
                _strict(raw, key)  # an unused key is checked too, as mysqlclient encodes it
        if not any(_needs_preparing(v, hex_mode) for v in params.values()):
            return params
        return {k: _prepare(v, raw, hex_mode) for k, v in params.items()}
    if isinstance(params, tuple | list):
        if not any(_needs_preparing(v, hex_mode) for v in params):
            return params
        return tuple(_prepare(v, raw, hex_mode) for v in params)
    return params


def _needs_preparing(value: Any, hex_mode: bool) -> bool:
    kind = type(value)
    if kind is float:
        return not isfinite(value)  # rejected by `_prepare`
    if kind is str:
        return not value.isascii()  # checked by `_prepare`: a lone surrogate is rejected
    if kind in _EXACT_LITERAL_TYPES:
        return False
    if isinstance(value, _BINARY_TYPES):
        return hex_mode or kind is not bytes
    if kind in _SEQUENCE_TYPES:
        return True  # a list becomes a tuple; a tuple's items may need it too
    if isinstance(value, str):
        return kind is not str  # a str subclass (a `str, Enum` member) nested in a tuple would
        # be sent as its `str()`, not its value (asyncmy escapes an exact str with the mode)
    if isinstance(value, (int, float, Decimal, *_SEQUENCE_TYPES)):
        return True  # a subclass: sent as its quoted text
    # a type asyncmy has no encoder for is sent as its `str()`, escaped with backslash rules
    # whatever the sql_mode; prepared so that the text is checked like every other string
    return True


def _check_finite(value: float) -> None:
    # mysqlclient rejects every non-finite float; asyncmy's escape_float only checks "inf" and
    # "nan", so "-inf" would reach the server as the SQL text `-infe0`.
    if not isfinite(value):
        raise ProgrammingError(f"{value!r} can not be used with MySQL")


def _prepare(value: Any, raw: Any, hex_mode: bool) -> Any:
    if not _needs_preparing(value, hex_mode):
        return value
    if type(value) is float:
        _check_finite(value)
    if type(value) is str:
        return _strict(raw, value)
    if hex_mode:
        return SqlLiteral(_render(raw, value))
    if isinstance(value, _BINARY_TYPES):
        return bytes(value)
    kind = type(value)
    if kind in _SET_TYPES:
        return _strict(raw, _set_text(value))
    if kind in _SEQUENCE_TYPES:
        return tuple(_prepare(v, raw, hex_mode) for v in value)
    if isinstance(value, str):
        return _strict(raw, str.__str__(value))  # the value itself, as an exact str
    return _strict(raw, _as_text(value))


def _set_text(value: Any) -> str:
    """A ``set`` of strings is one comma-joined string literal (a ``SET`` column value), as with
    mysqlclient (``Connection._set_literal``); any other element is a ``TypeError``."""
    if not all(isinstance(v, str) for v in value):
        raise TypeError("set elements must be strings")
    return ",".join(value)


def _as_text(value: Any) -> str:
    """A subclass of ``int``, ``float``, ``Decimal``, ``tuple``, ``list`` or ``set`` -> its text,
    which gets quoted.

    mysqlclient has no encoder for a subclass either and falls back to ``str(value)`` as a
    quoted string, so ``IntegerChoices.A`` is ``'1'`` and a NaN ``Decimal`` subclass is
    ``'NaN'`` (the server converts the string where a number is expected).
    """
    return str(value)


def _render(raw: Any, value: Any) -> str:
    """The SQL literal of ``value`` under ``NO_BACKSLASH_ESCAPES``."""
    kind = type(value)
    if kind is float:
        _check_finite(value)
    if kind not in _EXACT_LITERAL_TYPES:
        if isinstance(value, _BINARY_TYPES):
            return f"X'{bytes(value).hex()}'"
        if kind in _SET_TYPES:
            return raw.literal(_strict(raw, _set_text(value)))
        if kind in _SEQUENCE_TYPES:
            return "(" + ",".join(_render(raw, v) for v in value) + ")"
        if not isinstance(value, str):
            # A type asyncmy has no encoder for would be escaped with backslash rules: quote
            # its text instead.
            return "'" + raw.escape_string(_strict(raw, str(value))) + "'"
    if isinstance(value, str):
        _strict(raw, value)
    return raw.literal(value)  # follows the session's sql_mode (str, numbers, dates, ...)


def _format(raw: Any, cursor: Any, sql: str, params: Params) -> bytes:
    # Like Django's CursorWrapper: `params is None` means "no parameters" (no `%` processing).
    # Formatting and encoding are client side and happen before any I/O, so none of their errors
    # discards the connection: a bad placeholder or parameter count (`TypeError`) is a
    # `ProgrammingError` like with mysqlclient, a lone surrogate a `UnicodeEncodeError`.
    _strict(raw, sql)
    if params is None:
        return _encode(raw, sql)
    return _encode(raw, _format_prepared(cursor, sql, _prepare_params(params, raw)))


def _format_prepared(cursor: Any, sql: str, prepared: Params) -> str:
    try:
        return cursor.mogrify(sql, prepared)
    except TypeError as exc:
        raise ProgrammingError(str(exc)) from exc
    except asyncmy.errors.Error:
        with translate_errors():
            raise


def _strict(raw: Any, text: str) -> str:
    """``text`` if the connection's charset can encode it. mysqlclient encodes strictly and raises
    ``UnicodeEncodeError`` before any I/O; asyncmy encodes with ``surrogateescape``, so a lone
    ``U+DC80`` would reach the server as the byte 0x80. (asyncmy itself uses surrogates to carry
    the raw bytes of a ``bytes`` parameter, so the formatted statement cannot be checked as a
    whole: every ``str`` that comes from the caller is.)"""
    if not text.isascii():
        text.encode(raw._encoding)
    return text


def _encode(raw: Any, text: str) -> bytes:
    """The bytes asyncmy would send for ``text``, so that the batch planner measures real sizes
    and asyncmy (which does not encode ``bytes`` again) sends exactly them."""
    return text.encode(raw._encoding, "surrogateescape")


# --------------------------------------------------------------------------- batches
# Adapted from asyncmy/cursors.pyx::RE_INSERT_VALUES and Cursor._do_execute_many @ 0.2.18
# (Apache-2.0, https://github.com/long2ice/asyncmy; itself derived from PyMySQL, MIT); notices in
# THIRD_PARTY_NOTICES.md. Checked against mysqlclient's behaviour (GPL-2.0, not copied).
# Changes: lazy head, possessive blanks (linear time), no `AS alias` in the tail (mysqlclient runs
# those per row), a planner that builds and encodes every statement before any I/O.
_MAX_STATEMENT_BYTES = 64 * 1024  # a rewritten INSERT is split before it grows past this
_PLACEHOLDER = r"(?:%s|%\([^()]*\)s)"
# `INSERT | REPLACE ... VALUES (placeholders only) [ON DUPLICATE ...]`; anything else (an `AS`
# alias, `INSERT ... SELECT`, an `UPDATE`) runs once per parameter set.
_ROW_SHAPE = re.compile(
    rf"\s*+((?:INSERT|REPLACE)\b.+?\bVALUES?\s*+)"
    rf"(\(\s*+{_PLACEHOLDER}\s*+(?:,\s*+{_PLACEHOLDER}\s*+)*\))"
    r"(\s*+(?:ON DUPLICATE\b.*)?);?\s*+\Z",
    re.IGNORECASE | re.DOTALL,
)


def _plan_batch(raw: Any, sql: str, batch: Iterable[Params]) -> list[bytes]:
    """Every statement ``execute_many`` runs, built (and encoded) before any I/O."""
    _strict(raw, sql)
    batch = list(batch)  # Django and psycopg accept any iterable; check and prepare need two passes
    for params in batch:
        if params is not None and not isinstance(params, tuple | list | dict):
            raise ProgrammingError("the parameters of a batch must be tuples, lists or dicts")
    rows = [_prepare_params(p, raw) for p in batch]
    cursor = raw.cursor()
    shape = _ROW_SHAPE.match(sql) if rows and None not in rows else None
    if shape is None:
        return [
            _encode(raw, sql if row is None else _format_prepared(cursor, sql, row)) for row in rows
        ]
    head, template, tail = shape.groups()
    if "%" in head:
        head %= ()  # a literal '%' must be written '%%' there, as with mysqlclient
    head_bytes, tail_bytes = _encode(raw, head), _encode(raw, tail)
    groups = (_encode(raw, _format_prepared(cursor, template, row)) for row in rows)
    statements: list[bytes] = []
    current = bytearray(head_bytes)
    first = True
    for group in groups:
        if not first and len(current) + len(group) + len(tail_bytes) + 1 > _MAX_STATEMENT_BYTES:
            statements.append(bytes(current) + tail_bytes)
            current = bytearray(head_bytes)
            first = True
        if not first:
            current += b","
        current += group
        first = False
    statements.append(bytes(current) + tail_bytes)
    return statements


def _quote(sid: str) -> str:
    # Ported from django/db/backends/mysql/operations.py::quote_name @ 6.1.3 - unchanged.
    return sid if sid.startswith("`") and sid.endswith("`") else f"`{sid}`"
