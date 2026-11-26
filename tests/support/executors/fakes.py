"""A `RecordingDriver` that can hold statements and observe the registry, for executor tests.

The wrapper delegates every call to the recording connection (so queued results, the statement
log and the driver's own discard semantics stay those of `RecordingDriver`); it only adds a gate
in front of each statement and counters around the borrow.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from asyncopation.drivers.base import ExecResult, Params
from asyncopation.testing import RecordingDriver
from tests.support.lifecycle.fakes import FAKE_ENGINE, FakeEnv, FakeInfo

DEFAULT_ROWS: list[tuple[Any, ...]] = [(1,)]


class LostConnection(Exception):
    """What a driver with `disconnect_errors = (LostConnection,)` classifies as a lost connection."""


class Hold:
    """While installed on a driver, every statement stops at the gate until `release()`."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.entered = 0
        self._changed = asyncio.Event()

    def enter(self) -> None:
        self.entered += 1
        self._changed.set()

    async def wait_entered(self, count: int = 1) -> None:
        """Wait until `count` statements stand at the gate."""
        while self.entered < count:
            self._changed.clear()
            await self._changed.wait()

    def release(self) -> None:
        self.event.set()


class FaultyChunks:
    """Chunk iterator of a gated stream: a queued `chunk` fault is raised by the next read."""

    def __init__(self, driver: GatedDriver, inner: AsyncIterator[list[tuple[Any, ...]]]) -> None:
        self._driver = driver
        self._inner = inner

    def __aiter__(self) -> FaultyChunks:
        return self

    async def __anext__(self) -> list[tuple[Any, ...]]:
        driver = self._driver
        driver.raise_fault("chunk")
        if driver.chunk_hold is not None:
            driver.chunk_hold.enter()
            await driver.chunk_hold.event.wait()
        return await self._inner.__anext__()


class GatedConnection:
    """Delegating proxy of a `RecordingConnection`: gate first, then the recorded behaviour."""

    def __init__(self, driver: GatedDriver, inner: Any) -> None:
        self._driver = driver
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def _gate(self, sql: str | None = None) -> None:
        driver = self._driver
        driver.statement_calls += 1
        if driver.during is not None:
            driver.during_in_flight.append(driver.in_flight())
            await driver.during()
        if driver.hold is not None:
            driver.hold.enter()
            await driver.hold.event.wait()
        sql_hold = driver.sql_holds.get(sql) if sql is not None else None
        if sql_hold is not None:
            sql_hold.enter()
            await sql_hold.event.wait()

    async def _control_gate(self) -> None:
        driver = self._driver
        if driver.control_during is not None:
            await driver.control_during()
        if driver.control_hold is not None:
            driver.control_hold.enter()
            await driver.control_hold.event.wait()

    async def fetch_all(self, sql: str, params: Params) -> list[tuple[Any, ...]]:
        await self._gate(sql)
        return await self._inner.fetch_all(sql, params)

    async def execute(self, sql: str, params: Params) -> ExecResult:
        await self._gate(sql)
        return await self._inner.execute(sql, params)

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult:
        await self._gate(sql)
        return await self._inner.execute_many(sql, batch)

    @asynccontextmanager
    async def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AsyncGenerator[AsyncIterator[list[tuple[Any, ...]]]]:
        await self._gate(sql)
        async with self._inner.stream(sql, params, chunk) as chunks:
            self._driver.streams_open += 1
            try:
                yield FaultyChunks(self._driver, chunks)
            finally:
                try:
                    await self._driver.close_gate()
                finally:
                    self._driver.streams_open -= 1

    async def begin(self) -> None:
        await self._control_gate()
        self._driver.raise_fault("begin")
        await self._inner.begin()

    async def commit(self) -> None:
        await self._control_gate()
        await self._inner.commit()

    async def rollback(self) -> None:
        await self._control_gate()
        await self._inner.rollback()

    async def savepoint(self, sid: str) -> None:
        await self._control_gate()
        await self._inner.savepoint(sid)

    async def release_savepoint(self, sid: str) -> None:
        await self._control_gate()
        await self._inner.release_savepoint(sid)

    async def rollback_to_savepoint(self, sid: str) -> None:
        await self._control_gate()
        await self._inner.rollback_to_savepoint(sid)


class GatedDriver(RecordingDriver):
    """`RecordingDriver` for one alias: counts borrows, may hold statements, never runs dry."""

    def __init__(self, alias: str) -> None:
        super().__init__()
        self.alias = alias
        self.borrows = 0
        self.connections: list[GatedConnection] = []
        self.in_flight_at_borrow: list[int] = []
        self.statement_calls = 0
        self.streams_open = 0
        self.hold: Hold | None = None
        self.during: Callable[[], Awaitable[None]] | None = None
        """Awaited inside every statement, in the task that runs it (re-entrancy scenarios)."""
        self.during_in_flight: list[int] = []
        self.close_hold: Hold | None = None
        self.chunk_hold: Hold | None = None
        """Every chunk read of a stream stops here until `release()` (a slow server)."""
        self.control_hold: Hold | None = None
        """Every transaction-control call stops here until `release()`."""
        self.control_during: Callable[[], Awaitable[None]] | None = None
        """Awaited inside every transaction-control call (like `during` for statements)."""
        self.sql_holds: dict[str, Hold] = {}
        """A statement whose SQL is a key stops at that hold until it is released."""
        self.results_by_sql: dict[str, Any] = {}
        """A statement whose SQL is a key gets that result instead of the next queued one."""
        self.borrow_hold: Hold | None = None
        """`connection()` stops here once it holds the connection, until `release()` (a pin that
        waits in the pool)."""
        self.borrow_error: BaseException | None = None
        """Raised by `connection()` before a connection is handed out."""
        self._faults: dict[str, list[BaseException]] = {}

    def fail_next(self, kind: str, error: BaseException) -> None:
        """Make the next `kind` (`begin`, `chunk` or `stream_close`) raise `error`."""
        self._faults.setdefault(kind, []).append(error)

    def raise_fault(self, kind: str) -> None:
        queued = self._faults.get(kind)
        if queued:
            raise queued.pop(0)

    def hold_stream_closes(self) -> Hold:
        """Every stream close stops at the gate until `release()`."""
        self.close_hold = Hold()
        return self.close_hold

    async def close_gate(self) -> None:
        if self.close_hold is not None:
            self.close_hold.enter()
            await self.close_hold.event.wait()
        self.raise_fault("stream_close")

    def in_flight(self) -> int:
        from asyncopation.connection import registry

        return registry.in_flight(self.alias)

    def hold_chunks(self) -> Hold:
        """Every later chunk read stops at the gate until `release()`."""
        self.chunk_hold = Hold()
        return self.chunk_hold

    def hold_controls(self) -> Hold:
        """Every later transaction-control call stops at the gate until `release()`."""
        self.control_hold = Hold()
        return self.control_hold

    def hold_sql(self, sql: str) -> Hold:
        """Statements with exactly this SQL stop at the gate until `release()`."""
        hold = self.sql_holds[sql] = Hold()
        return hold

    def hold_borrows(self) -> Hold:
        self.borrow_hold = Hold()
        return self.borrow_hold

    def hold_statements(self) -> Hold:
        self.hold = Hold()
        return self.hold

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[Any]:
        self.borrows += 1
        self.in_flight_at_borrow.append(self.in_flight())
        if self.borrow_error is not None:
            raise self.borrow_error
        async with super().connection() as inner:
            if self.borrow_hold is not None:
                self.borrow_hold.enter()
                await self.borrow_hold.event.wait()
            wrapped = GatedConnection(self, inner)
            self.connections.append(wrapped)
            yield wrapped

    def _take(self, kind: str, sql: str, params: Any) -> Any:
        if sql in self.results_by_sql:
            self._queue.insert(0, self.results_by_sql[sql])
        elif not self._queue:  # an unqueued statement returns one row instead of failing
            self._queue.append(list(DEFAULT_ROWS))
        return super()._take(kind, sql, params)

    def kinds(self) -> list[str]:
        return [statement.kind for statement in self.statements]

    @property
    def released(self) -> list[bool]:
        return [connection.released for connection in self.connections]


async def opened_gated_driver(alias: str = "default") -> GatedDriver:
    """A `GatedDriver` that is already open (for tests of the driver on its own)."""
    driver = GatedDriver(alias)
    await driver.open()
    return driver


class ExecEnv:
    """Fake DATABASES (`default`, `other`) whose drivers are `GatedDriver`s, one per alias."""

    def __init__(self, fake: FakeEnv) -> None:
        self.fake = fake
        self.drivers = {"default": GatedDriver("default"), "other": GatedDriver("other")}

    @property
    def default(self) -> GatedDriver:
        return self.drivers["default"]

    @property
    def other(self) -> GatedDriver:
        return self.drivers["other"]


def install_executor_environment(fake: FakeEnv, monkeypatch: Any) -> ExecEnv:
    """Serve the gated drivers through the fake engine, then `configure()`."""
    from asyncopation.connection import configure, engines
    from asyncopation.connection.engines import Engine

    env = ExecEnv(fake)

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        return lambda: env.drivers[alias]

    def probe(driver: Any) -> FakeInfo:
        return FakeInfo(driver.capabilities.server_version)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> None:
        return None

    engine = Engine(prepare=prepare, probe=probe, inject=inject)
    monkeypatch.setitem(engines.FACTORIES, FAKE_ENGINE, lambda: engine)
    configure()
    return env


def drain_all(env: ExecEnv) -> None:
    from tests.support.lifecycle.checks import drain_in_flight

    for alias, driver in env.drivers.items():
        if driver.borrow_hold is not None:
            driver.borrow_hold.release()  # a failed test must not leave a borrow blocked
        drain_in_flight(alias)


def finish_executor_test(env: ExecEnv, *, leaves_in_flight: bool) -> None:
    """Teardown of an executor test: nothing may stay in flight unless the test opted out; whatever
    is left is finished so that it cannot hold back a later `close()`."""
    from asyncopation.connection import registry

    left = {alias: registry.in_flight(alias) for alias in env.drivers}
    drain_all(env)
    assert leaves_in_flight or not any(left.values()), f"work left in flight by the test: {left}"
