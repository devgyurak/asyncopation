"""Recording driver (ADR-0003 Decision 6): an ``AsyncDriver`` for unit-testing upper layers.

Results are queued up front (FIFO) and every call is logged, so sans-I/O cores and the engine can
be tested without a database. It does not count queries (that is built on observers, ADR-0005).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

from django.db import InterfaceError

from asyncopation.drivers.base import Capabilities, ExecResult, Params

type Row = tuple[Any, ...]
type Queued = list[Row] | ExecResult | BaseException

DEFAULT_CAPABILITIES = Capabilities(
    server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True
)


class Statement(NamedTuple):
    """One logged call. ``sql`` / ``params`` are ``None`` for transaction-control calls."""

    kind: str
    sql: str | None = None
    params: Params | Sequence[Params] = None


class NoQueuedResult(LookupError):
    """A statement ran but no result had been queued for it."""


class RecordingConnection:
    def __init__(self, driver: RecordingDriver) -> None:
        self._driver = driver
        self.in_transaction = False
        self.released = False  # set when the borrow ends; later calls raise InterfaceError

    def _check(self) -> None:
        if self.released:
            raise InterfaceError(
                "the connection was returned to the driver and can no longer be used"
            )

    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        self._check()
        item = self._driver._take("fetch_all", sql, params)
        return (item.rows or []) if isinstance(item, ExecResult) else item

    async def execute(self, sql: str, params: Params) -> ExecResult:
        self._check()
        item = self._driver._take("execute", sql, params)
        if isinstance(item, ExecResult):
            return item
        return ExecResult(rowcount=len(item), rows=item)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        self._check()
        item = self._driver._take("execute_many", sql, batch)
        return item if isinstance(item, ExecResult) else ExecResult(rowcount=len(item))

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        self._check()
        item = self._driver._take("stream", sql, params)
        rows = (item.rows or []) if isinstance(item, ExecResult) else item
        yield _GuardedChunks(self, rows, chunk)

    async def begin(self) -> None:
        self._check()
        self._driver._log(Statement("begin"))
        self.in_transaction = True

    async def commit(self) -> None:
        self._check()
        self._driver._log(Statement("commit"))
        self.in_transaction = False
        self._driver._raise_queued_error()

    async def rollback(self) -> None:
        self._check()
        self._driver._log(Statement("rollback"))
        self.in_transaction = False
        self._driver._raise_queued_error()

    async def savepoint(self, sid: str) -> None:
        self._check()
        self._driver._log(Statement("savepoint", sid))

    async def release_savepoint(self, sid: str) -> None:
        self._check()
        self._driver._log(Statement("release_savepoint", sid))

    async def rollback_to_savepoint(self, sid: str) -> None:
        self._check()
        self._driver._log(Statement("rollback_to_savepoint", sid))


class RecordingDriver:
    """``AsyncDriver`` with queued results and a statement log shared by all its connections."""

    def __init__(self, capabilities: Capabilities | None = None) -> None:
        self._capabilities = capabilities or DEFAULT_CAPABILITIES
        self._queue: list[Queued] = []
        self._statements: list[Statement] = []
        self.opened = False
        self.closed = False
        self.disconnect_errors: tuple[type[BaseException], ...] = ()

    @property
    def capabilities(self) -> Capabilities:
        """Like the real drivers: known only while the driver is open."""
        if not self.opened:
            raise RuntimeError("RecordingDriver is not open: capabilities are known after open()")
        return self._capabilities

    @property
    def statements(self) -> list[Statement]:
        """Snapshot of the statement log (a new list on every read)."""
        return list(self._statements)

    def queue(self, *results: Queued) -> None:
        """Append results; each statement that returns data pops the next one."""
        self._queue.extend(results)

    async def open(self) -> None:
        self.opened, self.closed = True, False

    async def close(self) -> None:
        self.opened, self.closed = False, True

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[RecordingConnection]:
        if not self.opened:
            raise RuntimeError("RecordingDriver is not open")
        connection = RecordingConnection(self)
        try:
            yield connection
        finally:
            if connection.in_transaction:  # a real driver rolls back before pooling the connection
                self._log(Statement("rollback"))
            connection.released = True

    def is_disconnect(self, exc: BaseException) -> bool:
        return isinstance(exc, self.disconnect_errors)

    def _log(self, statement: Statement) -> None:
        self._statements.append(statement)

    def _raise_queued_error(self) -> None:
        """``commit()`` / ``rollback()`` consume a queued exception (e.g. an ``IntegrityError`` that
        a deferred constraint raises at COMMIT); any other queued item is left for statements."""
        if self._queue and isinstance(self._queue[0], BaseException):
            error = self._queue.pop(0)
            assert isinstance(error, BaseException)
            raise error

    def _take(
        self, kind: str, sql: str, params: Params | Sequence[Params]
    ) -> list[Row] | ExecResult:
        self._log(Statement(kind, sql, _frozen(params)))
        if not self._queue:
            raise NoQueuedResult(f"no queued result for {kind}: {sql}")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _frozen(params: Any) -> Any:
    """Snapshot of the parameters so a later mutation by the caller cannot rewrite the log."""
    if isinstance(params, dict):
        return dict(params)
    if isinstance(params, list):
        return list(params)
    return params


class _GuardedChunks:
    """Chunk iterator of a recorded stream; unusable once its connection's borrow has ended."""

    def __init__(self, connection: RecordingConnection, rows: list[Row], size: int) -> None:
        self._connection = connection
        self._rows = rows
        self._size = size
        self._position = 0

    def __aiter__(self) -> _GuardedChunks:
        return self

    async def __anext__(self) -> list[Row]:
        self._connection._check()  # even an exhausted iterator reports a released connection
        if self._position >= len(self._rows):
            raise StopAsyncIteration
        chunk = self._rows[self._position : self._position + self._size]
        self._position += self._size
        return chunk
