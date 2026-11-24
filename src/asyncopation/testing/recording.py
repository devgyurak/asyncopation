"""Recording driver (ADR-0003 Decision 6): an ``AsyncDriver`` for unit-testing upper layers.

Results are queued up front (FIFO) and every call is logged, so sans-I/O cores and the engine can
be tested without a database. It does not count queries (that is built on observers, ADR-0005).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

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

    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        item = self._driver._take("fetch_all", sql, params)
        return (item.rows or []) if isinstance(item, ExecResult) else item

    async def execute(self, sql: str, params: Params) -> ExecResult:
        item = self._driver._take("execute", sql, params)
        if isinstance(item, ExecResult):
            return item
        return ExecResult(rowcount=len(item), rows=item)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        item = self._driver._take("execute_many", sql, batch)
        return item if isinstance(item, ExecResult) else ExecResult(rowcount=len(item))

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        item = self._driver._take("stream", sql, params)
        rows = (item.rows or []) if isinstance(item, ExecResult) else item
        yield _chunks(rows, chunk)

    async def begin(self) -> None:
        self._driver._log(Statement("begin"))

    async def commit(self) -> None:
        self._driver._log(Statement("commit"))

    async def rollback(self) -> None:
        self._driver._log(Statement("rollback"))

    async def savepoint(self, sid: str) -> None:
        self._driver._log(Statement("savepoint", sid))

    async def release_savepoint(self, sid: str) -> None:
        self._driver._log(Statement("release_savepoint", sid))

    async def rollback_to_savepoint(self, sid: str) -> None:
        self._driver._log(Statement("rollback_to_savepoint", sid))


class RecordingDriver:
    """``AsyncDriver`` with queued results and a statement log shared by all its connections."""

    def __init__(self, capabilities: Capabilities | None = None) -> None:
        self._capabilities = capabilities or DEFAULT_CAPABILITIES
        self._queue: list[Queued] = []
        self.statements: list[Statement] = []
        self.opened = False
        self.closed = False
        self.disconnect_errors: tuple[type[BaseException], ...] = ()

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    def queue(self, *results: Queued) -> None:
        """Append results; each statement that returns data pops the next one."""
        self._queue.extend(results)

    async def open(self) -> None:
        self.opened, self.closed = True, False

    async def close(self) -> None:
        self.opened, self.closed = False, True

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[RecordingConnection]:
        yield RecordingConnection(self)

    def is_disconnect(self, exc: BaseException) -> bool:
        return isinstance(exc, self.disconnect_errors)

    def _log(self, statement: Statement) -> None:
        self.statements.append(statement)

    def _take(
        self, kind: str, sql: str, params: Params | Sequence[Params]
    ) -> list[Row] | ExecResult:
        self._log(Statement(kind, sql, params))
        if not self._queue:
            raise NoQueuedResult(f"no queued result for {kind}: {sql}")
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


async def _chunks(rows: list[Row], size: int) -> AsyncIterator[list[Row]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]
