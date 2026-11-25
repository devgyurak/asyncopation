"""Executors: how a statement reaches a connection (ADR-0001, ADR-0002).

``BorrowedExecutor`` borrows a pooled connection per statement, so concurrent ``gather`` branches
run on different connections. ``PinnedExecutor`` keeps one connection for a whole block (the
future ``async_atomic``, #19) and belongs to the task that opened it (the owner rule, C2): use from
another task, re-entrant use while a statement or stream is in flight and use after the block
exited raise ``ConcurrentQueryError``. ``current_executor(alias)`` picks one.

Accounting. Every executor gets its driver from ``lifecycle.ensure_driver`` and calls
``registry.statement_started`` right after it returns, with no ``await`` in between
(``statement_started`` does not check for a closing ``close()``, ADR-0004). A pin is one in-flight
unit for its whole lifetime (ADR-0001 Decision 3 amendment), a borrowed statement or stream one for
its own lifetime; ``close(timeout)`` therefore waits for open pins and streams.

Every statement goes through ``_run``: the single place where the ADR-0005 hooks run (#4).

The owner is a task, so the pinned executor is stored in a contextvar *mapping* (alias -> executor)
that is replaced, never mutated: a child task copies the context, sees the executor and is
rejected by the owner check instead of silently borrowing another connection. No counter lives in
a contextvar (children copy the context).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import weakref
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from types import MappingProxyType, TracebackType
from typing import Any, Protocol

from asyncopation._django.transaction import TransactionManagementError
from asyncopation.connection import registry
from asyncopation.connection.lifecycle import ensure_driver
from asyncopation.drivers.base import AsyncDbConnection, AsyncDriver, ExecResult, Params
from asyncopation.errors import ConcurrentQueryError

logger = logging.getLogger("asyncopation.connection")

type Row = tuple[Any, ...]
type Chunks = AbstractAsyncContextManager[AsyncIterator[list[Row]]]

__all__ = ["BorrowedExecutor", "Executor", "PinnedExecutor", "current_executor", "pin"]

OWNER_MESSAGE = (
    "queries inside async_atomic must run in the task that opened it; "
    "move concurrent work outside the transaction"
)
_BUSY_MESSAGE = (
    "a statement or stream is already in flight on this pinned connection; await it (or close the "
    "stream) before running another query"
)
_CLOSED_MESSAGE = (
    "the pinned connection was returned to the pool when its block exited and can no longer be used"
)
_REPIN_MESSAGE = (
    "this database is already pinned by another task, which owns the transaction; a block cannot "
    "open an independent one on the same alias in a different task - move concurrent work "
    "outside the transaction"
)
_BROKEN_MESSAGE = (
    "An error occurred in the current transaction. You can't execute queries until the end of "
    "the 'atomic' block."
)


class Executor(Protocol):
    """Runs statements on behalf of one alias; both implementations speak driver-level types."""

    @property
    def alias(self) -> str: ...

    @property
    def pinned(self) -> bool: ...

    async def fetch_all(self, sql: str, params: Params) -> list[Row]: ...

    async def execute(self, sql: str, params: Params) -> ExecResult: ...

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult: ...

    def stream(self, sql: str, params: Params, chunk: int) -> Chunks: ...


# -- the choke point -------------------------------------------------------------------------
def _run(connection: AsyncDbConnection, kind: str, sql: str, params: Any, chunk: int = 0) -> Any:
    """The one place every statement passes (ADR-0005 hooks run here, #4). Returns an awaitable
    (``fetch_all`` / ``execute`` / ``execute_many``) or the stream's context manager."""
    if kind == "stream":
        return connection.stream(sql, params, chunk)
    return getattr(connection, kind)(sql, params)


class _Unit:
    """One in-flight unit of the registry, finished exactly once (a stream finalizer or a pin exit
    may race to finish it; the registry clamps a double finish and would hide the bug)."""

    __slots__ = ("alias", "done")

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.done = False
        registry.statement_started(alias)

    def finish(self) -> None:
        if not self.done:
            self.done = True
            registry.statement_finished(self.alias)


# -- borrowed --------------------------------------------------------------------------------
class BorrowedExecutor:
    """Borrows a pooled connection for each statement (outside a transaction)."""

    __slots__ = ("_alias",)
    pinned = False

    def __init__(self, alias: str) -> None:
        self._alias = alias

    @property
    def alias(self) -> str:
        return self._alias

    async def _statement(self, kind: str, sql: str, params: Any) -> Any:
        alias = self._alias
        driver = await ensure_driver(alias)
        registry.statement_started(alias)  # no await since ensure_driver returned
        try:
            async with driver.connection() as connection:
                return await _run(connection, kind, sql, params)
        finally:
            registry.statement_finished(alias)

    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        return await self._statement("fetch_all", sql, params)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        return await self._statement("execute", sql, params)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        return await self._statement("execute_many", sql, batch)

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[Row]]]:
        """Holds the connection (and the in-flight count) until the block ends."""
        alias = self._alias
        driver = await ensure_driver(alias)
        registry.statement_started(alias)
        try:
            async with (
                driver.connection() as connection,
                _run(connection, "stream", sql, params, chunk) as chunks,
            ):
                yield chunks
        finally:
            registry.statement_finished(alias)


# -- pinned ----------------------------------------------------------------------------------
class _PinnedStream:
    """A stream on a pinned connection: exempt from the owner check when closing (async generator
    finalizers run in another task), unusable once closed or once its pin block ended.

    The executor is busy from ``__aenter__`` until the stream is closed, so a stream that is created
    but never entered blocks nothing.
    """

    def __init__(self, executor: PinnedExecutor, sql: str, params: Params, chunk: int) -> None:
        self._executor = executor
        self._statement = (sql, params, chunk)
        self._manager: Chunks | None = None
        self._chunks: AsyncIterator[list[Row]] | None = None
        self._finished: asyncio.Future[None] | None = None  # set once a close has begun
        self.closed = False

    async def __aenter__(self) -> _PinnedStream:
        executor = self._executor
        if self.closed or self._manager is not None:
            raise RuntimeError("a stream can be entered only once")
        connection = executor._check()
        executor._busy = True
        try:
            sql, params, chunk = self._statement
            manager = _run(connection, "stream", sql, params, chunk)
            self._chunks = await manager.__aenter__()
            self._manager = manager
        except BaseException as exc:
            self.closed = True  # a stream that failed to open is not entered again
            executor._busy = False
            executor._statement_failed(exc)
            raise
        executor._streams.add(self)
        return self

    def __aiter__(self) -> _PinnedStream:
        return self

    async def __anext__(self) -> list[Row]:
        executor = self._executor
        if self.closed or executor.closed:
            raise ConcurrentQueryError(_CLOSED_MESSAGE)
        executor._check_owner()
        assert self._chunks is not None
        try:
            return await self._chunks.__anext__()
        except StopAsyncIteration:
            raise
        except BaseException as exc:
            executor._statement_failed(exc)
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return await self.close(exc_type, exc, tb)

    async def close(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        tb: TracebackType | None = None,
    ) -> bool | None:
        """Idempotent; never raises on a closed executor and skips the owner check. A second call
        waits for the first one to finish, so the pin never returns its connection while a
        stream is still being closed."""
        if self._finished is not None:
            await asyncio.shield(self._finished)
            return None
        self.closed = True
        if self._manager is None:  # never entered: nothing to close
            self._finished = asyncio.get_running_loop().create_future()
            self._finished.set_result(None)
            return None
        finished = self._finished = asyncio.get_running_loop().create_future()
        executor = self._executor
        executor._streams.discard(self)
        executor._closing.add(self)
        try:
            assert self._manager is not None
            return await self._manager.__aexit__(exc_type, exc, tb)
        except BaseException as failure:
            executor._statement_failed(failure)
            raise
        finally:
            if exc is not None:
                executor._statement_failed(exc)
            executor._closing.discard(self)
            executor._busy = False
            finished.set_result(None)

    async def wait_closed(self) -> None:
        if self._finished is not None:
            await asyncio.shield(self._finished)


class PinnedExecutor:
    """One connection for a whole block, owned by the task that opened it (ADR-0001 C2).

    Created by ``pin``. Transaction control passes straight through to the connection after the
    owner check; policy (nesting, durable, ``on_commit``, ``needs_rollback``) is #19's.
    """

    pinned = True

    def __init__(
        self,
        alias: str,
        driver: AsyncDriver,
        connection: AsyncDbConnection,
        owner: asyncio.Task[Any],
    ) -> None:
        self._alias = alias
        self._driver = driver
        self._connection: AsyncDbConnection | None = connection
        self._owner = weakref.ref(owner)
        self._busy = False
        self._in_transaction = False
        self._streams: set[_PinnedStream] = set()  # open streams
        self._closing: set[_PinnedStream] = set()  # streams whose close is under way
        self.closed = False
        self.broken = False
        self.depth = 1

    @property
    def alias(self) -> str:
        return self._alias

    # -- checks --------------------------------------------------------------------------
    def _is_owner(self) -> bool:
        task, owner = asyncio.current_task(), self._owner()
        return task is not None and task is owner

    def _check_owner(self) -> None:
        if self.closed:
            raise ConcurrentQueryError(_CLOSED_MESSAGE)
        if not self._is_owner():
            raise ConcurrentQueryError(OWNER_MESSAGE)

    def _check(self) -> AsyncDbConnection:
        self._check_owner()
        if self._busy:
            raise ConcurrentQueryError(_BUSY_MESSAGE)
        if self.broken:
            raise TransactionManagementError(_BROKEN_MESSAGE)
        assert self._connection is not None
        return self._connection

    def _statement_failed(self, exc: BaseException) -> None:
        """A cancellation (or a lost connection) inside a transaction ends it: the driver closes
        the connection (driver contract, ADR-0003), so later statements must not run."""
        if self._in_transaction and (
            isinstance(exc, asyncio.CancelledError)
            or (isinstance(exc, Exception) and self._driver.is_disconnect(exc))
        ):
            self.broken = True

    # -- statements ----------------------------------------------------------------------
    async def _statement(self, kind: str, sql: str, params: Any) -> Any:
        connection = self._check()
        self._busy = True
        try:
            return await _run(connection, kind, sql, params)
        except BaseException as exc:
            self._statement_failed(exc)
            raise
        finally:
            self._busy = False

    async def fetch_all(self, sql: str, params: Params) -> list[Row]:
        return await self._statement("fetch_all", sql, params)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        return await self._statement("execute", sql, params)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        return await self._statement("execute_many", sql, batch)

    def stream(self, sql: str, params: Params, chunk: int) -> _PinnedStream:
        """Busy from entering the stream until it is closed: another query meanwhile raises
        (ADR-0001 Decision 7)."""
        self._check()  # fail early; the stream checks again when it is entered
        return _PinnedStream(self, sql, params, chunk)

    # -- transaction control (passthroughs, no policy) ------------------------------------
    async def _control(self, name: str, *args: str, ends_transaction: bool = False) -> None:
        connection = self._check()
        self._busy = True
        try:
            await getattr(connection, name)(*args)
        except BaseException as exc:
            self._statement_failed(exc)
            raise
        finally:
            self._busy = False
            if ends_transaction:  # even a failed COMMIT / ROLLBACK ended the transaction
                self._in_transaction = False

    async def begin(self) -> None:
        previous = self._in_transaction
        self._in_transaction = True  # a cancelled BEGIN may have left the connection discarded
        try:
            await self._control("begin")
        except Exception:
            self._in_transaction = previous
            raise

    async def commit(self) -> None:
        await self._control("commit", ends_transaction=True)

    async def rollback(self) -> None:
        """No-op on a broken transaction (the connection is already discarded): cleanup code must
        not replace the error that broke it."""
        self._check_owner()
        try:
            if not self.broken:
                await self._close_streams()  # a stream still open must not skip the ROLLBACK
        finally:
            if self.broken:  # already broken, or breaking while its streams were closed
                self._in_transaction = False
            else:
                await self._control("rollback", ends_transaction=True)

    async def savepoint(self, sid: str) -> None:
        await self._control("savepoint", sid)

    async def release_savepoint(self, sid: str) -> None:
        await self._control("release_savepoint", sid)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self._control("rollback_to_savepoint", sid)

    # -- teardown ------------------------------------------------------------------------
    async def _close_streams(self) -> None:
        """Close every open stream and wait for closes under way; the first error is raised after
        all of them were tried."""
        failures: list[BaseException] = []
        for stream in [*self._streams, *self._closing]:
            try:
                await stream.close()
                await stream.wait_closed()
            except BaseException as exc:
                failures.append(exc)
        if failures:  # a cancellation must not be hidden behind an ordinary error
            raise next((f for f in failures if isinstance(f, asyncio.CancelledError)), failures[0])

    async def _invalidate(self) -> None:
        """The block exited: refuse further use and close the streams still open."""
        self.closed = True
        try:
            await self._close_streams()
        finally:
            self._connection = None


# -- pin and selection -----------------------------------------------------------------------
_pinned: contextvars.ContextVar[Mapping[str, PinnedExecutor]] = contextvars.ContextVar(
    "asyncopation_pinned", default=MappingProxyType({})
)
_borrowed: dict[str, BorrowedExecutor] = {}


@asynccontextmanager
async def pin(alias: str) -> AsyncGenerator[PinnedExecutor]:
    """Pin one connection of ``alias`` to the current task for the block (the future
    ``async_atomic``). The owner pinning the same alias again reuses the executor; another task
    doing so raises ``ConcurrentQueryError``; another alias is independent."""
    mapping = _pinned.get()
    existing = mapping.get(alias)
    if existing is not None and not existing.closed:
        if not existing._is_owner():
            raise ConcurrentQueryError(_REPIN_MESSAGE)
        existing.depth += 1
        try:
            yield existing
        finally:
            existing.depth -= 1
        return
    task = asyncio.current_task()
    if task is None:  # pragma: no cover - a coroutine always runs inside a task
        raise RuntimeError("pin() needs a running asyncio task")
    driver = await ensure_driver(alias)
    unit = _Unit(alias)  # no await since ensure_driver returned
    try:
        async with driver.connection() as connection:
            executor = PinnedExecutor(alias, driver, connection, task)
            token = _pinned.set(MappingProxyType({**mapping, alias: executor}))
            try:
                try:
                    yield executor
                except BaseException:
                    try:
                        await executor._invalidate()
                    except Exception as failure:  # the block's own error wins
                        logger.error("asyncopation: closing a pinned block failed: %r", failure)
                    raise
                await executor._invalidate()
            finally:
                with suppress(ValueError):  # exited from another Context: nothing to restore
                    _pinned.reset(token)
    finally:
        unit.finish()


def current_executor(alias: str) -> Executor:
    """The pinned executor of ``alias`` in this context (even a closed one: using it raises),
    else the alias's borrowed executor."""
    found = _pinned.get().get(alias)
    if found is not None:
        return found
    borrowed = _borrowed.get(alias)
    if borrowed is None:
        borrowed = _borrowed[alias] = BorrowedExecutor(alias)
    return borrowed
