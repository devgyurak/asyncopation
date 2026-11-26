"""Instrumentation hooks: synchronous query observers (ADR-0005, #4).

Every call to a connection - statements, streams and transaction control - passes the executors'
``_run`` (``connection.executor``). When somebody observes, ``_run`` hands the call to
``observed_call``, which emits a ``query_started`` event right before the driver call (the
connection is already acquired; a call that never reaches the driver emits nothing) and a
``query_finished`` event when it returns, fails or is cancelled (a stream: when it closes).

Observers are synchronous callables (async-correctness rule 6): they add no ``await`` and schedule
nothing; an observer that raises an ``Exception`` is logged and the query goes on (a
``BaseException`` propagates once every observer got the event, and never counts as a failure of
the connection: the pinned executor learns what the driver itself did from an ``Outcome``).
Subscribers live in two immutable
tuples - one process-global (``add_observer`` / ``remove_observer``), one in a contextvar
(``with observe(fn):``, so a ``gather`` child sees its parent's observers and nothing leaks to a
sibling). An executor snapshots both when a call starts (``active()``) and the finished event goes
to that same snapshot, even when a stream is closed by a finalizer in another task or context.
With no subscriber the cost is the two empty-tuple checks of ``active()``.

Parameters are in an event only for observers registered with ``params=True``, or for everyone
while ``settings.DEBUG`` is true (parameters may hold secrets).
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from types import TracebackType
from typing import Any, Literal

from django.conf import settings

from asyncopation.drivers.base import AsyncDbConnection, ExecResult

logger = logging.getLogger("asyncopation.connection")

__all__ = [
    "EVENT_KINDS",
    "Outcome",
    "QueryEvent",
    "active",
    "add_observer",
    "observe",
    "observed_call",
    "remove_observer",
]

type Row = tuple[Any, ...]
type EventName = Literal["query_started", "query_finished"]
type EventKind = Literal["query", "transaction"]
type Observer = Callable[[QueryEvent], object]

EVENT_KINDS: dict[str, EventKind] = {
    "fetch_all": "query",
    "execute": "query",
    "execute_many": "query",
    "stream": "query",
    "savepoint": "query",  # ADR-0005 Decision 2: SAVEPOINT statements count as queries
    "release_savepoint": "query",
    "rollback_to_savepoint": "query",
    "begin": "transaction",  # connection-level BEGIN / COMMIT / ROLLBACK
    "commit": "transaction",
    "rollback": "transaction",
}
"""The ADR-0005 event kind of every connection call that passes ``_run``."""

_CONTROL_SQL = {
    "begin": "BEGIN",
    "commit": "COMMIT",
    "rollback": "ROLLBACK",
    "savepoint": "SAVEPOINT {}",
    "release_savepoint": "RELEASE SAVEPOINT {}",
    "rollback_to_savepoint": "ROLLBACK TO SAVEPOINT {}",
}
"""Descriptive SQL of transaction control: the driver API takes no SQL (``savepoint(sid)``), so the
event text is backend-neutral and unquoted, not the exact bytes the driver sent."""


@dataclass(frozen=True, slots=True)
class QueryEvent:
    """One ADR-0005 event. Times are seconds (``time.perf_counter``).

    ``queue_time`` is the wait for a pooled connection (0 on a pinned connection); ``query_time``
    runs from right before the driver call until it returned (a stream: until it closed, so it
    includes the time the consumer spent between chunks); ``total_time`` is their sum. A started
    event has ``rowcount``, ``error``, ``query_time`` and ``total_time`` set to ``None``.

    ``rowcount``: ``fetch_all`` the number of rows returned, ``execute`` / ``execute_many``
    ``ExecResult.rowcount`` as is (``-1`` means unknown), a stream the rows read until it closed,
    transaction control and any failed call ``None``. ``error`` is the exception that made the
    call fail (a cancellation included); for a stream only a failure of the driver (open, a chunk
    read, close) or a cancellation - an exception of the consuming code is not a query error.
    ``params`` is ``None`` unless the observer asked for parameters or ``settings.DEBUG`` is true.
    """

    name: EventName
    alias: str
    kind: EventKind
    sql: str
    params: Any
    many: bool
    rowcount: int | None
    error: BaseException | None
    queue_time: float
    query_time: float | None
    total_time: float | None


class _Subscription:
    """One registration. A scoped ``observe()`` removes its own registration by identity;
    ``remove_observer`` matches the function by equality (a bound method recreated on each access
    is equal, not identical), most recent first; the same function may be registered twice.
    ``exited`` is set when its ``observe()`` block exits: a Context that still holds it (the block
    exited in another Context) stops reporting to it."""

    __slots__ = ("exited", "fn", "params")

    def __init__(self, fn: Observer, params: bool) -> None:
        self.fn = fn
        self.params = params
        self.exited = False


type Subscribers = tuple[_Subscription, ...]

_global: Subscribers = ()
_scoped: contextvars.ContextVar[Subscribers] = contextvars.ContextVar(
    "asyncopation_observers", default=()
)


def active() -> Subscribers:
    """The subscribers a call starting now reports to: global ones first, then the scoped ones of
    this context, in registration order. ``()`` when nobody observes (two empty-tuple checks).

    A scoped registration whose block already exited in another Context is dropped here, and from
    this Context's tuple, so it neither reports nor accumulates."""
    scoped = _scoped.get()
    if scoped:
        for subscription in scoped:
            if subscription.exited:
                scoped = tuple(s for s in scoped if not s.exited)
                _scoped.set(scoped)
                break
    if not _global:
        return scoped
    return _global + scoped if scoped else _global


def _subscription(fn: Observer, params: bool) -> _Subscription:
    if not callable(fn):
        raise TypeError(f"an observer must be callable, got {fn!r}")
    if inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(type(fn).__call__):
        raise TypeError(
            f"observers are synchronous callables (ADR-0005); {fn!r} is a coroutine function"
        )
    return _Subscription(fn, bool(params))


def add_observer(fn: Observer, *, params: bool = False) -> None:
    """Register ``fn`` for every query of the process (all tasks, all loops) until
    ``remove_observer(fn)``. ``params=True`` delivers the statement parameters.

    A startup-time call, like ``configure()``: the tuple is replaced without a lock (``threading``
    is banned in ``src``), so do not register from several threads at once."""
    global _global
    _global = (*_global, _subscription(fn, params))


def remove_observer(fn: Observer) -> None:
    """Unregister the most recent global registration of ``fn``; ``ValueError`` when there is
    none. Calls already started still report to it (their subscribers were snapshotted)."""
    global _global
    for index in range(len(_global) - 1, -1, -1):
        if _global[index].fn == fn:
            _global = _global[:index] + _global[index + 1 :]
            return
    raise ValueError(f"{fn!r} is not a registered global observer")


class _Observation:
    """The single-use context manager ``observe()`` returns.

    Exiting removes this registration by identity from the current tuple instead of resetting a
    token: a token reset would bring back a block that exited out of order, and fails when the
    block exits in another Context (an async generator finalized by another task). The
    registration is also marked exited, so a Context that still holds it stops reporting to it
    (``active()``). Calls already started keep their snapshot."""

    __slots__ = ("_entered", "_subscription")

    def __init__(self, subscription: _Subscription) -> None:
        self._subscription = subscription
        self._entered = False

    def __enter__(self) -> _Observation:
        if self._entered:
            raise RuntimeError("an observe() block can be entered only once; call observe() again")
        self._entered = True
        _scoped.set((*_scoped.get(), self._subscription))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        subscription = self._subscription
        if subscription.exited:
            return
        subscription.exited = True
        current = _scoped.get()
        if subscription in current:
            _scoped.set(tuple(s for s in current if s is not subscription))


def observe(fn: Observer, *, params: bool = False) -> _Observation:
    """``with observe(fn):`` - ``fn(event)`` is called for every query started in this task (and
    the tasks it starts) until the block exits. ``fn`` is synchronous; ``params=True`` delivers the
    statement parameters (otherwise they are ``None`` unless ``settings.DEBUG``)."""
    return _Observation(_subscription(fn, params))


# -- emitting --------------------------------------------------------------------------------
def _emit(subscribers: Subscribers, full: QueryEvent, bare: QueryEvent) -> None:
    """Call every observer. An ``Exception`` is logged; a ``BaseException`` (a cancellation,
    ``KeyboardInterrupt``, a test framework's failure) is never swallowed, but the remaining
    observers still get the event first (an observer counting started/finished pairs stays
    balanced), then the first one is raised."""
    abort: BaseException | None = None
    for subscription in subscribers:
        try:
            result = subscription.fn(full if subscription.params else bare)
        except Exception:
            logger.exception("asyncopation: observer %r failed on %s", subscription.fn, full.name)
            continue
        except BaseException as exc:
            if abort is None:
                abort = exc
            elif exc is not abort:  # never lost: attached to the one that is raised
                abort.add_note(f"asyncopation: another observer of {full.name} also raised {exc!r}")
            continue
        if result is not None and inspect.isawaitable(result):
            _discard_awaitable(subscription.fn, result, full.name)
    if abort is not None:
        raise abort


class Outcome:
    """What the driver call of one observed call did, for the pinned executor (ADR-0001 6c).

    With observers, an exception leaving ``_run`` may come from an observer instead of the
    connection; only what the driver itself raised may break a transaction, and a call an observer
    stopped before the driver changed no transaction state. ``reached`` is set right before the
    driver call, ``failure`` to the exception the driver call (or a stream's open, chunk read or
    close) raised."""

    __slots__ = ("failure", "reached")

    def __init__(self) -> None:
        self.reached = False
        self.failure: BaseException | None = None


def _finish_after(call: _Call, original: BaseException, error: BaseException | None) -> None:
    """``call.finish`` on a failure path: an observer that raises there never replaces the
    original exception (async-correctness rule 4); it is attached as a note (unless it re-raised
    the original itself)."""
    try:
        call.finish(None, error)
    except BaseException as late:
        if late is not original:
            original.add_note(f"asyncopation: a query_finished observer also raised {late!r}")


def _discard_awaitable(fn: Observer, result: Any, name: str) -> None:
    """An observer returned an awaitable: it is never awaited (observers are synchronous), so
    close a coroutine (no "never awaited" warning) and log the misuse."""
    if inspect.iscoroutine(result):
        result.close()
    logger.error(
        "asyncopation: observer %r returned an awaitable on %s; observers are synchronous and "
        "it was not awaited",
        fn,
        name,
    )


def _describe(kind: str, args: tuple[Any, ...]) -> tuple[str, Any, bool]:
    """``(sql, params, many)`` of one connection call."""
    template = _CONTROL_SQL.get(kind)
    if template is not None:
        return template.format(*args), None, False
    return args[0], args[1], kind == "execute_many"


def _rowcount(kind: str, result: Any) -> int | None:
    if kind == "fetch_all":
        return len(result)
    if isinstance(result, ExecResult):
        return result.rowcount
    return None


class _Call:
    """One observed call: its subscriber snapshot, its started event and its finish (once)."""

    __slots__ = ("_bare", "_done", "_event", "_started_at", "_subscribers")

    def __init__(
        self,
        subscribers: Subscribers,
        alias: str,
        queued_at: float | None,
        kind: str,
        args: tuple[Any, ...],
    ) -> None:
        sql, params, many = _describe(kind, args)
        self._subscribers = subscribers
        self._done = False
        acquired_at = perf_counter()
        queue_time = 0.0 if queued_at is None else acquired_at - queued_at
        # Settings that are not configured or not loaded yet (an upper-layer test without Django)
        # count as DEBUG off: the safe direction, and no settings import on the event loop.
        hide = params is not None and not (settings.configured and settings.DEBUG)
        if hide and not any(s.params for s in subscribers):
            params, hide = None, False  # nobody may see them: one event, without parameters
        event = self._event = QueryEvent(
            "query_started",
            alias,
            EVENT_KINDS[kind],
            sql,
            params,
            many,
            None,
            None,
            queue_time,
            None,
            None,
        )
        self._bare = _without_params(event) if hide else event
        try:
            _emit(subscribers, event, self._bare)
        except BaseException as exc:  # an observer aborted the call: it never reaches the driver
            self._started_at = perf_counter()
            _finish_after(self, exc, exc)
            raise
        self._started_at = perf_counter()  # the started observers' own time is in neither time

    def finish(self, rowcount: int | None, error: BaseException | None) -> None:
        if self._done:
            return
        self._done = True
        query_time = perf_counter() - self._started_at
        started, bare = self._event, self._bare
        full = QueryEvent(
            "query_finished",
            started.alias,
            started.kind,
            started.sql,
            started.params,
            started.many,
            None if error is not None else rowcount,
            error,
            started.queue_time,
            query_time,
            started.queue_time + query_time,
        )
        _emit(self._subscribers, full, _without_params(full) if bare is not started else full)


def _without_params(event: QueryEvent) -> QueryEvent:
    return QueryEvent(
        event.name,
        event.alias,
        event.kind,
        event.sql,
        None,
        event.many,
        event.rowcount,
        event.error,
        event.queue_time,
        event.query_time,
        event.total_time,
    )


async def _observed_statement(
    connection: AsyncDbConnection,
    subscribers: Subscribers,
    alias: str,
    queued_at: float | None,
    kind: str,
    args: tuple[Any, ...],
    outcome: Outcome | None,
) -> Any:
    call = _Call(subscribers, alias, queued_at, kind, args)  # may stop the call (BaseException)
    if outcome is not None:
        outcome.reached = True
    try:
        result = await getattr(connection, kind)(*args)
    except BaseException as exc:
        if outcome is not None:
            outcome.failure = exc
        _finish_after(call, exc, exc)
        raise
    call.finish(_rowcount(kind, result), None)
    return result


class _CountingChunks:
    """The chunk iterator of an observed stream: counts rows and remembers a failed read.

    A plain class, not an async generator (which would need its own finalizer)."""

    __slots__ = ("_inner", "error", "rows")

    def __init__(self, inner: AsyncIterator[list[Row]]) -> None:
        self._inner = inner
        self.rows = 0
        self.error: BaseException | None = None

    def __aiter__(self) -> _CountingChunks:
        return self

    async def __anext__(self) -> list[Row]:
        try:
            chunk = await self._inner.__anext__()
        except StopAsyncIteration:
            raise
        except BaseException as exc:
            self.error = exc
            raise
        self.rows += len(chunk)
        return chunk


class _ObservedStream:
    """The async context manager of an observed stream (``_PinnedStream`` drives its
    ``__aenter__`` / ``__aexit__`` directly). Holds its subscriber snapshot, so the finished event
    reaches the same observers when a finalizer closes it from another task."""

    __slots__ = (
        "_alias",
        "_args",
        "_call",
        "_chunks",
        "_connection",
        "_manager",
        "_outcome",
        "_queued_at",
        "_subscribers",
    )

    def __init__(
        self,
        connection: AsyncDbConnection,
        subscribers: Subscribers,
        alias: str,
        queued_at: float | None,
        args: tuple[Any, ...],
        outcome: Outcome | None,
    ) -> None:
        self._connection = connection
        self._outcome = outcome
        self._subscribers = subscribers
        self._alias = alias
        self._queued_at = queued_at
        self._args = args
        self._call: _Call | None = None
        self._manager: Any = None
        self._chunks: _CountingChunks | None = None

    async def __aenter__(self) -> _CountingChunks:
        call = self._call = _Call(
            self._subscribers, self._alias, self._queued_at, "stream", self._args
        )
        outcome = self._outcome
        if outcome is not None:
            outcome.reached = True
        try:
            manager = self._manager = self._connection.stream(*self._args)
            chunks = self._chunks = _CountingChunks(await manager.__aenter__())
        except BaseException as exc:
            if outcome is not None:
                outcome.failure = exc
            _finish_after(call, exc, exc)
            raise
        return chunks

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        call, chunks = self._call, self._chunks
        assert call is not None
        assert chunks is not None
        try:
            suppress = await self._manager.__aexit__(exc_type, exc, tb)
        except BaseException as failure:
            if self._outcome is not None:
                self._outcome.failure = failure
            _finish_after(call, failure, _stream_error(chunks, exc, failure))
            raise
        try:
            call.finish(chunks.rows, _stream_error(chunks, exc, None))
        except BaseException as late:
            if exc is None or suppress:
                raise
            if late is not exc:
                exc.add_note(f"asyncopation: a query_finished observer also raised {late!r}")
        return suppress  # the block's own exception, if any, propagates unchanged


def _stream_error(
    chunks: _CountingChunks, exc: BaseException | None, failure: BaseException | None
) -> BaseException | None:
    """What failed the stream: the driver's close, else a failed chunk read, else a cancellation of
    the block. ``GeneratorExit`` and errors of the consuming code are a normal close."""
    if failure is not None and failure is not exc:
        return failure
    if chunks.error is not None:
        return chunks.error
    if isinstance(exc, asyncio.CancelledError):
        return exc
    return None


def observed_call(
    connection: AsyncDbConnection,
    subscribers: Subscribers,
    alias: str,
    queued_at: float | None,
    kind: str,
    args: Sequence[Any],
    outcome: Outcome | None = None,
) -> Any:
    """The observed form of ``getattr(connection, kind)(*args)``: an awaitable, or the async
    context manager of a stream. ``queued_at`` is when the executor asked for a pooled connection
    (``None`` on a pinned connection); ``outcome``, when given, records whether the call reached
    the driver and what the driver raised."""
    if kind == "stream":
        return _ObservedStream(connection, subscribers, alias, queued_at, tuple(args), outcome)
    return _observed_statement(
        connection, subscribers, alias, queued_at, kind, tuple(args), outcome
    )
