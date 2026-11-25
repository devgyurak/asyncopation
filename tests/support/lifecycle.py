"""Fakes, spies and scenarios for the lifecycle / registry / probe tests (ADR-0004, issue #5, PR #52).

Everything under test is imported lazily inside functions, so that a missing module makes the
tests that need it fail (not the collection of unrelated tests), and so that the isolated
subprocess scripts can import this module without pulling in psycopg.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import logging
import ssl
import threading
import uuid
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Literal, NamedTuple
from unittest import mock

from asyncopation.compile.server_info import ServerInfo
from asyncopation.drivers.base import Capabilities, PoolStats

FAKE_ENGINE = "tests.fake.engine"
UNSUPPORTED_ENGINE = "django.db.backends.sqlite3"
PG_ENGINE = "django.db.backends.postgresql"
POSTGIS_ENGINE = "django.contrib.gis.db.backends.postgis"
LAZY_LOGGER = "asyncopation.connection"
DEFAULT_STATS = PoolStats(in_use=1, idle=2, waiting=3)
FAKE_VERSION = (17, 2)


# --------------------------------------------------------------------------- fakes
class FakeOpenError(Exception):
    """Raised by the fake driver's `open()` while the script says so."""


class FakeProbeError(Exception):
    """Raised by the fake engine's `probe()` while the script says so."""


class FakeCloseError(Exception):
    """Raised by the fake driver's `close()` when the script says so."""


class FakeInjectError(Exception):
    """Raised by the fake engine's `inject()` while the script says so."""


@dataclass(frozen=True, slots=True)
class FakeInfo(ServerInfo):
    version: tuple[int, ...]


@dataclass
class Script:
    """Controls the failures and numbers of the fake engine."""

    fail_opens: int = 0
    fail_probes: int = 0
    fail_close: bool = False
    fail_closes: int = 0  # the next N `close()` calls raise (others succeed)
    fail_open_after: int | None = None  # `open()` raises once this many opens succeeded
    fail_injects: int = 0
    gate: asyncio.Event | None = None  # `open()` waits for it (created in the running loop)
    gate_after: int | None = None  # the gate only holds opens once this many succeeded
    gate_driver_index: int | None = None  # the gate only holds the N-th driver built
    version: tuple[int, ...] = FAKE_VERSION
    stats: PoolStats = DEFAULT_STATS


class Prepared(NamedTuple):
    alias: str
    name: str
    use_tz: bool
    time_zone: str


@dataclass
class Recorder:
    """Counts what the engine and its drivers were asked to do."""

    prepares: list[Prepared] = field(default_factory=list)
    built: list[Any] = field(default_factory=list)
    open_attempts: int = 0
    opens: int = 0
    closes: int = 0
    probes: int = 0
    injects: list[tuple[str, Any]] = field(default_factory=list)

    def last_prepare(self, alias: str) -> Prepared:
        return next(p for p in reversed(self.prepares) if p.alias == alias)


class FakeDriver:
    def __init__(self, recorder: Recorder, script: Script, index: int = 0) -> None:
        self._recorder = recorder
        self._script = script
        self.index = index
        self.is_open = False

    @property
    def capabilities(self) -> Capabilities:
        if not self.is_open:
            raise RuntimeError("not open")
        return Capabilities(
            server_version=self._script.version, server_side_cursors=True, atomic_execute_many=True
        )

    async def open(self) -> None:
        self._recorder.open_attempts += 1
        held = self._script.gate_after is None or self._recorder.opens >= self._script.gate_after
        only = self._script.gate_driver_index
        held = held and (only is None or only == self.index)
        if self._script.gate is not None and held:
            await self._script.gate.wait()
        await asyncio.sleep(0)  # a real open awaits: concurrent callers interleave here
        after = self._script.fail_open_after
        if after is not None and self._recorder.opens >= after:
            raise FakeOpenError("boom: open")
        if self._script.fail_opens > 0:
            self._script.fail_opens -= 1
            raise FakeOpenError("boom: open")
        self.is_open = True
        self._recorder.opens += 1

    async def close(self) -> None:
        self._recorder.closes += 1
        self.is_open = False
        if self._script.fail_closes > 0:
            self._script.fail_closes -= 1
            raise FakeCloseError("boom: close")
        if self._script.fail_close:
            raise FakeCloseError("boom: close")

    def pool_stats(self) -> PoolStats:
        return self._script.stats if self.is_open else PoolStats(0, 0, 0)


def make_engine(recorder: Recorder, script: Script) -> Any:
    from asyncopation.connection.engines import Engine

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        recorder.prepares.append(Prepared(alias, settings_dict["NAME"], use_tz, time_zone))

        def factory() -> FakeDriver:
            driver = FakeDriver(recorder, script, len(recorder.built))
            recorder.built.append(driver)
            return driver

        return factory

    def probe(driver: Any) -> FakeInfo:
        recorder.probes += 1
        if script.fail_probes > 0:
            script.fail_probes -= 1
            raise FakeProbeError("boom: probe")
        return FakeInfo(driver.capabilities.server_version)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> None:
        if script.fail_injects > 0:
            script.fail_injects -= 1
            raise FakeInjectError("boom: inject")
        recorder.injects.append((alias, info))

    return Engine(prepare=prepare, probe=probe, inject=inject)


# --------------------------------------------------------------------------- environment
def database_entry(name: str, engine: str) -> dict[str, Any]:
    from tests.support.compile_wrappers import PG_SETTINGS

    return {**copy.deepcopy(PG_SETTINGS), "ENGINE": engine, "NAME": name}


class FakeEnv:
    """Fake DATABASES (aliases `default` and `other`) served by a fake engine factory."""

    def __init__(self, monkeypatch: Any) -> None:
        from asyncopation.connection import engines

        self._monkeypatch = monkeypatch
        self.recorder = Recorder()
        self.script = Script()
        self.names = {"default": "fake_db", "other": "fake_other_db"}
        self.engines = {"default": FAKE_ENGINE, "other": FAKE_ENGINE}
        self.options: dict[str, dict[str, Any]] = {}
        self.updates: dict[str, dict[str, Any]] = {}
        engine = make_engine(self.recorder, self.script)
        monkeypatch.setitem(engines.FACTORIES, FAKE_ENGINE, lambda: engine)
        self._apply()

    def _apply(self) -> None:
        from django.db import connections

        databases = {}
        for alias, name in self.names.items():
            entry = database_entry(name, self.engines[alias])
            entry["OPTIONS"] = dict(self.options.get(alias, {}))
            entry.update(self.updates.get(alias, {}))
            databases[alias] = entry
        self._monkeypatch.setitem(connections.__dict__, "settings", databases)

    def rename(self, alias: str, name: str) -> None:
        self.names[alias] = name
        self._apply()

    def use_unsupported_engine(self, alias: str) -> None:
        self.use_engine(alias, UNSUPPORTED_ENGINE)

    def use_engine(self, alias: str, engine: str) -> None:
        self.engines[alias] = engine
        self._apply()

    def add_alias(self, alias: str, name: str, engine: str) -> None:
        self.names[alias] = name
        self.engines[alias] = engine
        self._apply()

    def add_broken_alias(self, alias: str, error: Exception) -> None:
        """An alias whose engine factory raises `error` when `configure()` builds it."""
        from asyncopation.connection import engines

        def factory() -> Any:
            raise error

        key = f"tests.broken.{alias}"
        self._monkeypatch.setitem(engines.FACTORIES, key, factory)
        self.add_alias(alias, f"{alias}_db", key)

    def override(self, alias: str, **settings_dict: Any) -> None:
        """Set DATABASES[alias][key] = value for every given key."""
        self.updates.setdefault(alias, {}).update(settings_dict)
        self._apply()

    def set_options(self, alias: str, options: dict[str, Any]) -> None:
        self.options[alias] = options
        self._apply()


def uncopiable_options() -> dict[str, Any]:
    """OPTIONS leaves that `copy.deepcopy` rejects (an SSL context and an object with a lock)."""
    return {
        "ssl": ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        "lock": threading.Lock(),
        "nested": {"hosts": ["a", "b"]},
    }


def install_fake_environment(monkeypatch: Any) -> FakeEnv:
    from asyncopation.connection import registry

    registry.reset()
    return FakeEnv(monkeypatch)


async def dispose_environment() -> None:
    """Per-test teardown of the fake environment: asserts that no driver leaked (see below)."""
    await teardown_lifecycle()


# --------------------------------------------------------------------------- scenarios
def entry_aliases() -> list[str]:
    from asyncopation.connection import registry

    return sorted(alias for alias, _loop in registry.entries())


async def ensure_many(alias: str, count: int) -> list[Any]:
    from asyncopation.connection.lifecycle import ensure_driver

    return await asyncio.gather(*(ensure_driver(alias) for _ in range(count)))


def distinct(objects: list[Any]) -> int:
    return len({id(o) for o in objects})


async def open_then_close(alias: str = "default") -> None:
    from asyncopation.connection.lifecycle import close, ensure_driver

    await ensure_driver(alias)
    await close()


async def ensure_is_refused(alias: str) -> bool:
    from asyncopation.connection.lifecycle import ensure_driver

    try:
        await ensure_driver(alias)
    except RuntimeError:
        return True
    return False


async def in_flight_in_other_loop(alias: str) -> int:
    from asyncopation.connection import registry

    async def probe() -> int:
        return registry.in_flight(alias)

    return await run_in_thread_loop(probe())


def fresh_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    loop.close()
    return loop


@dataclass
class SecondLoop:
    driver: Any
    loop: asyncio.AbstractEventLoop
    entries_inside: list[tuple[str, Any]]
    entries_after_close: list[tuple[str, Any]]


async def open_in_second_loop(alias: str = "default") -> SecondLoop:
    """Open `alias` in a new event loop on a thread, then close that loop's drivers."""

    async def run() -> SecondLoop:
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close, ensure_driver

        driver = await ensure_driver(alias)
        inside = registry.entries()
        await close()
        return SecondLoop(driver, asyncio.get_running_loop(), inside, registry.entries())

    return await run_in_thread_loop(run())


async def _is_closing() -> bool:
    from asyncopation.connection import registry

    return registry.is_closing(asyncio.get_running_loop())


class ClosingScenario:
    """A `close()` that is held back by one statement in flight on `alias`."""

    def __init__(self, alias: str, timeout: float) -> None:
        self.alias = alias
        self.timeout = timeout
        self.task: asyncio.Task[None] | None = None

    async def begin(self) -> None:
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close
        from tests.support.aio import wait_until

        registry.statement_started(self.alias)
        self.task = asyncio.create_task(close(timeout=self.timeout))
        await wait_until(_is_closing)

    async def join(self) -> None:
        assert self.task is not None
        await self.task

    async def cancel_close(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def finish(self) -> None:
        from asyncopation.connection import registry

        with contextlib.suppress(Exception):
            registry.statement_finished(self.alias)
        await self.join()

    async def cleanup(self) -> None:
        with contextlib.suppress(BaseException):
            await self.finish()


@dataclass
class LockCounter:
    created: int = 0
    acquired: int = 0


def count_locks(monkeypatch: Any) -> LockCounter:
    counter = LockCounter()

    class CountingLock(asyncio.Lock):
        def __init__(self) -> None:
            super().__init__()
            counter.created += 1

        async def acquire(self) -> Literal[True]:
            counter.acquired += 1
            return await super().acquire()

    monkeypatch.setattr(asyncio, "Lock", CountingLock)
    return counter


def lazy_warnings(caplog: Any) -> list[str]:
    """Messages logged at WARNING or above by the connection package's logger."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno >= logging.WARNING
    ]


def capture_logs(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG, logger=LAZY_LOGGER)


def forget_wrapper(alias: str) -> None:
    from asyncopation.compile import postgresql

    postgresql._WRAPPERS.pop(alias, None)


def injected_wrapper(alias: str) -> Any:
    from asyncopation.compile import postgresql

    return postgresql._WRAPPERS.get(alias)


def pg_engine() -> Any:
    from asyncopation.connection.engines import FACTORIES

    return FACTORIES[PG_ENGINE]()


# --------------------------------------------------------------------------- ASGI
class FakeApp:
    """The wrapped ASGI app: records every delegated call."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], Any, Any]] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.calls.append((scope, receive, send))


@dataclass
class LifespanRun:
    sent: list[dict[str, Any]]
    snapshots: dict[str, list[str]]  # message type -> registry aliases at that moment

    @property
    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def run_lifespan(app: Any) -> LifespanRun:
    """Drive `app` through startup then shutdown; snapshot the registry at every message."""
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    run = LifespanRun([], {})

    async def receive() -> dict[str, Any]:
        return next(events)

    async def send(message: dict[str, Any]) -> None:
        run.sent.append(message)
        run.snapshots[message["type"]] = entry_aliases()

    await app({"type": "lifespan"}, receive, send)
    return run


async def call_with_scope(app: Any, scope: dict[str, Any]) -> tuple[Any, Any]:
    async def receive() -> dict[str, Any]:
        return {"type": "noop"}

    async def send(message: dict[str, Any]) -> None:
        return None

    await app(scope, receive, send)
    return receive, send


# --------------------------------------------------------------------------- real-engine spies
class CountingDriver:
    """Delegates to a real driver and counts open/close (psycopg itself is never patched)."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    async def open(self) -> None:
        self._recorder.open_attempts += 1
        await self._inner.open()
        self._recorder.opens += 1

    async def close(self) -> None:
        self._recorder.closes += 1
        await self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def install_spy_engine(monkeypatch: Any, recorder: Recorder) -> None:
    """Wrap the real PostgreSQL engine so that every prepare/build/open/probe is recorded."""
    from asyncopation.connection import engines

    real = engines.FACTORIES[PG_ENGINE]()

    def prepare(alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str):
        recorder.prepares.append(Prepared(alias, settings_dict["NAME"], use_tz, time_zone))
        inner = real.prepare(alias, settings_dict, use_tz=use_tz, time_zone=time_zone)

        def factory() -> CountingDriver:
            driver = CountingDriver(inner(), recorder)
            recorder.built.append(driver)
            return driver

        return factory

    def probe(driver: Any) -> Any:
        recorder.probes += 1
        return real.probe(driver)

    def inject(alias: str, settings_dict: dict[str, Any], info: Any) -> None:
        recorder.injects.append((alias, info))
        real.inject(alias, settings_dict, info)

    spy = dataclasses.replace(real, prepare=prepare, probe=probe, inject=inject)
    monkeypatch.setitem(engines.FACTORIES, PG_ENGINE, lambda: spy)


async def teardown_lifecycle() -> None:
    """Close what the test left open on this loop, then assert nothing is left, then reset.

    A driver left open in ANOTHER loop (or one `close()` could not remove) fails the check; the
    registry is reset either way so that one leak does not cascade into the next test.
    """
    from asyncopation.connection import registry
    from asyncopation.connection.lifecycle import close

    try:
        await close(timeout=1.0)
        leaked = [*registry.entries(), *registry.take_leaked()]
        assert leaked == [], f"drivers left open in the registry after the test: {leaked}"
    finally:
        registry.reset()


async def leave_open_in_other_loop(alias: str = "default") -> None:
    """Open `alias` in a throwaway loop on a thread and return WITHOUT closing it (a leak)."""
    from asyncopation.connection.lifecycle import ensure_driver

    async def run() -> None:
        await ensure_driver(alias)

    await run_in_thread_loop(run())


async def run_in_thread_loop(coroutine: Any, within: float = 15.0) -> Any:
    """Run `coroutine` to completion in a new event loop on a worker thread (bounded)."""
    async with asyncio.timeout(within):
        return await asyncio.to_thread(asyncio.run, coroutine)


@contextlib.contextmanager
def patched_alias_settings(alias: str = "default", **overrides: Any) -> Generator[None]:
    """Django's configured settings with `overrides` applied to `alias` (e.g. a wrong PASSWORD)."""
    from django.db import connections

    databases = copy.deepcopy(dict(connections.settings))
    databases[alias].update(overrides)
    with mock.patch.dict(connections.__dict__, {"settings": databases}):
        yield


def live_database_name(alias: str = "default") -> str:
    from django.db import connections

    return connections.settings[alias]["NAME"]


def configured_database_names(recorder: Recorder) -> list[str]:
    return [p.name for p in recorder.prepares]


def pg_version_number(server_version: tuple[int, ...]) -> int:
    return server_version[0] * 10000 + server_version[1]


@dataclass
class FailedThenRecovered:
    error: BaseException | None
    server_info_after_failure: Any
    entries_after_failure: list[tuple[str, Any]]
    entries_after_recovery: list[tuple[str, Any]]
    server_info_after_recovery: Any


async def fail_first_open_then_recover(alias: str = "default") -> FailedThenRecovered:
    """Open with a wrong password (fails), then reconfigure with the right one and open again."""
    from asyncopation.connection import configure, registry
    from asyncopation.connection.lifecycle import ensure_driver

    error: BaseException | None = None
    with patched_alias_settings(alias, PASSWORD="definitely-wrong"):
        configure()
        try:
            await ensure_driver(alias)
        except Exception as exc:
            error = exc
    after = (registry.server_info(alias), registry.entries())
    configure()
    await ensure_driver(alias)
    return FailedThenRecovered(
        error, after[0], after[1], registry.entries(), registry.server_info(alias)
    )


async def rename_then_restore(recorder: Recorder) -> None:
    """configure() against a renamed database, then again against the live (test) name."""
    from asyncopation.connection import configure

    with patched_alias_settings("default", NAME="renamed_before_test_db"):
        configure()
    configure()


def compile_attempts_with_injected_wrapper(alias: str) -> int:
    """Sync connection attempts while compiling a query with the wrapper `setup()` injected."""
    from asyncopation.compile.scope import compile_scope
    from harness.catalog.models import Book
    from tests.support.connect_counter import count_sync_connection_attempts

    wrapper = injected_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        compile_scope(alias, wrapper, autocommit=True),
    ):
        Book.objects.filter(title="book-1").query.sql_with_params()
        wrapper.pg_version  # noqa: B018 - Django reads it for version-dependent SQL
    return counter.count


def configure_session() -> None:
    """Session start (after the test DB exists): re-run `configure()` with the test DB name."""
    from asyncopation.connection import configure, registry

    registry.reset()
    configure()


def assert_registry_empty_at_session_end() -> None:
    from asyncopation.connection import registry

    leaked = registry.entries()
    registry.reset()
    assert leaked == [], f"drivers left open in the registry at session end: {leaked}"


def aliases_of(entries: list[tuple[str, Any]]) -> list[str]:
    """The alias of every registry entry, in order."""
    return [alias for alias, _loop in entries]


def prepared_aliases(recorder: Recorder) -> list[str]:
    return [p.alias for p in recorder.prepares]


def prepared_names(recorder: Recorder) -> list[str]:
    return [p.name for p in recorder.prepares]


# --------------------------------------------------------------------------- more scenarios
def counts(recorder: Recorder) -> dict[str, int]:
    return {
        "built": len(recorder.built),
        "opens": recorder.opens,
        "closes": recorder.closes,
        "probes": recorder.probes,
        "injects": len(recorder.injects),
    }


async def ensure_many_settled(alias: str, count: int) -> list[Any]:
    """Like `ensure_many` but errors are returned in the list instead of raised."""
    from asyncopation.connection.lifecycle import ensure_driver

    return await asyncio.gather(
        *(ensure_driver(alias) for _ in range(count)), return_exceptions=True
    )


def errors_in(results: list[Any]) -> list[BaseException]:
    return [r for r in results if isinstance(r, BaseException)]


def drivers_in(results: list[Any]) -> list[Any]:
    return [r for r in results if not isinstance(r, BaseException)]


async def setup_and_ensure_concurrently(alias: str) -> tuple[None, Any]:
    from asyncopation.connection.lifecycle import ensure_driver, setup

    return await asyncio.gather(setup([alias]), ensure_driver(alias))


async def _two_acquires(counter: LockCounter) -> bool:
    return counter.acquired >= 2


async def _three_acquires(counter: LockCounter) -> bool:
    return counter.acquired >= 3


async def _opener_started(recorder: Recorder) -> bool:
    return recorder.open_attempts >= 1


class OpeningScenario:
    """A first `ensure_driver` that is held inside `driver.open()` by the script's gate."""

    def __init__(
        self, env: FakeEnv, alias: str = "default", counter: LockCounter | None = None
    ) -> None:
        self.env = env
        self.alias = alias
        self.counter = counter  # counts lock acquisitions: a second one means a parked waiter
        self.task: asyncio.Task[Any] | None = None

    async def begin(self) -> None:
        from asyncopation.connection.lifecycle import ensure_driver
        from tests.support.aio import wait_until

        self.env.script.gate = asyncio.Event()
        self.task = asyncio.create_task(ensure_driver(self.alias))
        await wait_until(lambda: _opener_started(self.env.recorder))

    async def cancel(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.env.script.gate = None

    async def finish(self) -> Any:
        assert self.task is not None
        gate, self.env.script.gate = self.env.script.gate, None
        if gate is not None:
            gate.set()
        return await self.task

    async def cleanup(self) -> None:
        with contextlib.suppress(BaseException):
            await self.finish()


async def reopen_after_cancelled_first_opener(scenario: OpeningScenario) -> Any:
    """Cancel the opener stuck in `open()`, then let the next caller open the driver."""
    from asyncopation.connection.lifecycle import ensure_driver

    await scenario.cancel()
    return await ensure_driver(scenario.alias)


def all_mention(messages: list[str], fragment: str) -> bool:
    return all(fragment in message for message in messages)


def last_prepared_name(recorder: Recorder, alias: str = "default") -> str:
    return recorder.last_prepare(alias).name


async def attempts_reading_pg_version_without_injection(alias: str) -> int:
    """Positive control for the tripwire: a compile wrapper that was never injected tries to
    connect when its server version is read."""
    from django.db import connections

    from asyncopation.compile.postgresql import PostgreSQLCompileWrapper
    from tests.support.connect_counter import count_sync_connection_attempts

    wrapper = PostgreSQLCompileWrapper(dict(connections.settings[alias]), alias)
    with count_sync_connection_attempts() as counter, contextlib.suppress(BaseException):
        wrapper.pg_version  # noqa: B018 - the read is the attempt
    return counter.count


def drain_in_flight(alias: str) -> None:
    """Finish every statement a test started (so that teardown can close without waiting)."""
    from asyncopation.connection import registry

    while registry.in_flight(alias) > 0:
        registry.statement_finished(alias)


# --------------------------------------------------------------------------- live foreign loop
class ThreadLoop:
    """An event loop that keeps running on its own thread (a second, live loop)."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> ThreadLoop:
        self.thread.start()
        return self

    async def run(self, coroutine: Any, within: float = 15.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        async with asyncio.timeout(within):
            return await asyncio.wrap_future(future)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


async def hold_driver_in_live_loop(thread_loop: ThreadLoop, alias: str = "default") -> None:
    from asyncopation.connection.lifecycle import ensure_driver

    await thread_loop.run(ensure_driver(alias))


async def release_live_loop(thread_loop: ThreadLoop) -> None:
    """Close that loop's drivers (in it) and stop it."""
    from asyncopation.connection.lifecycle import close

    await thread_loop.run(close())
    thread_loop.stop()


# --------------------------------------------------------------------------- more scenarios (2)
def error_names(results: list[Any]) -> list[str]:
    """The class name of every raised error in `results`, `ok` for a value."""
    return [type(r).__name__ if isinstance(r, BaseException) else "ok" for r in results]


async def close_while_opening(scenario: OpeningScenario) -> list[Any]:
    """`close()` while the first open is held in `driver.open()`; then let that open finish."""
    from asyncopation.connection.lifecycle import close
    from tests.support.aio import wait_until

    assert scenario.task is not None
    close_task = asyncio.create_task(close())
    await wait_until(_is_closing)
    gate, scenario.env.script.gate = scenario.env.script.gate, None
    assert gate is not None
    gate.set()
    return list(await asyncio.gather(scenario.task, close_task, return_exceptions=True))


async def waiter_meets_closing(scenario: OpeningScenario) -> list[Any]:
    """A second caller queues on the open lock, `close()` begins, then the open is released."""
    from asyncopation.connection.lifecycle import close, ensure_driver
    from tests.support.aio import wait_until

    assert scenario.task is not None
    assert scenario.counter is not None
    counter = scenario.counter
    waiter = asyncio.create_task(ensure_driver(scenario.alias))
    await wait_until(lambda: _two_acquires(counter))  # the waiter is parked behind the opener
    close_task = asyncio.create_task(close())
    await wait_until(_is_closing)
    gate, scenario.env.script.gate = scenario.env.script.gate, None
    assert gate is not None
    gate.set()
    return list(await asyncio.gather(scenario.task, waiter, close_task, return_exceptions=True))


class SetupScenario:
    """`setup(aliases)` held inside the second `driver.open()` (the first one succeeded)."""

    def __init__(self, env: FakeEnv, aliases: list[str]) -> None:
        self.env = env
        self.aliases = aliases
        self.task: asyncio.Task[None] | None = None

    async def begin(self) -> None:
        from asyncopation.connection.lifecycle import setup
        from tests.support.aio import wait_until

        self.env.script.gate = asyncio.Event()
        self.env.script.gate_after = 1
        self.task = asyncio.create_task(setup(self.aliases))
        await wait_until(lambda: _second_open_started(self.env.recorder))

    async def cancel(self) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.env.script.gate = None


async def _second_open_started(recorder: Recorder) -> bool:
    return recorder.open_attempts >= 2


class OverlappingClose:
    """Close B waits for a statement in flight; close A (short timeout) starts later and ends first."""

    def __init__(self, alias: str = "default") -> None:
        self.alias = alias
        self.b: asyncio.Task[None] | None = None

    async def begin(self) -> list[Any]:
        """Start B, then run A to its timeout; returns A's outcome."""
        from asyncopation.connection import registry
        from asyncopation.connection.lifecycle import close
        from tests.support.aio import wait_until

        registry.statement_started(self.alias)
        self.b = asyncio.create_task(close(timeout=5.0))
        await wait_until(_is_closing)
        return list(await asyncio.gather(close(timeout=0.05), return_exceptions=True))

    async def finish(self) -> None:
        from asyncopation.connection import registry

        with contextlib.suppress(Exception):
            registry.statement_finished(self.alias)
        if self.b is not None:
            await asyncio.gather(self.b, return_exceptions=True)


def errors_logged(caplog: Any) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno >= logging.ERROR
    ]


def info_messages(caplog: Any) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LAZY_LOGGER and r.levelno == logging.INFO
    ]


def mention_count(messages: list[str], fragment: str) -> int:
    return sum(1 for m in messages if fragment in m)


async def lifespan_with(app: Any) -> LifespanRun:
    return await run_lifespan(app)


def fake_driver(version: tuple[int, ...]) -> Any:
    """A stand-in driver exposing only `capabilities.server_version` (what `probe` reads)."""
    from types import SimpleNamespace

    return SimpleNamespace(capabilities=SimpleNamespace(server_version=version))


def injected_aliases(recorder: Recorder) -> list[str]:
    return [alias for alias, _info in recorder.injects]


# --------------------------------------------------------------------------- real server checks
def new_application_name() -> str:
    return f"asyncopation-test-{uuid.uuid4().hex[:12]}"


@contextlib.contextmanager
def application_name_override(name: str, alias: str = "default") -> Generator[None]:
    """Run with `OPTIONS["application_name"] = name` for `alias` (so its sessions can be counted)."""
    from django.db import connections

    options = {**connections.settings[alias].get("OPTIONS", {}), "application_name": name}
    with patched_alias_settings(alias, OPTIONS=options):
        yield


async def server_connections(name: str) -> int:
    """Sessions on the server whose `application_name` is `name` (pg_stat_activity)."""
    from tests.support.drivers.admin import admin_scalar

    return int(
        await admin_scalar(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s", [name]
        )
    )


async def _none_left(name: str) -> bool:
    return await server_connections(name) == 0


async def server_connections_after_settling(name: str) -> int:
    """`server_connections`, once the server has reaped the closed sessions (polled, bounded)."""
    from tests.support.aio import wait_until

    await wait_until(lambda: _none_left(name), within=10.0)
    return await server_connections(name)


async def current_database_of_driver(alias: str = "default") -> str:
    """`SELECT current_database()` on a connection borrowed from the registry's driver."""
    from asyncopation.connection.lifecycle import ensure_driver

    driver = await ensure_driver(alias)
    async with driver.connection() as connection:
        rows = await connection.fetch_all("SELECT current_database()", None)
    return rows[0][0]


async def ensure_after_rename(alias: str = "default") -> Any:
    """`configure()` against the live name, rename `NAME`, then open: raises (fail closed)."""
    from asyncopation.connection import configure
    from asyncopation.connection.lifecycle import ensure_driver

    configure()
    with patched_alias_settings(alias, NAME="renamed_after_configure"):
        return await ensure_driver(alias)


def assert_registry_clean() -> None:
    """Fail when a test left a driver in the registry (or one was dropped with a closed loop)."""
    from asyncopation.connection import registry

    leftovers = [*registry.entries(), *registry.take_leaked()]
    registry.reset()
    assert leftovers == [], f"the test left drivers in the registry: {leftovers}"


def uncopiable_snapshot_facts() -> dict[str, bool]:
    """`configured_databases()` on settings whose OPTIONS cannot be deep-copied."""
    from django.db import connections

    from asyncopation._django.connections import configured_databases

    options = uncopiable_options()
    original = {"default": {**database_entry("x", FAKE_ENGINE), "OPTIONS": options}}
    with mock.patch.dict(connections.__dict__, {"settings": original}):
        snapshot = configured_databases()
    copied = snapshot["default"]["OPTIONS"]
    return {
        "leaf_is_shared": copied["lock"] is options["lock"] and copied["ssl"] is options["ssl"],
        "options_dict_is_copied": copied is not options,
        "nested_list_is_copied": copied["nested"]["hosts"] is not options["nested"]["hosts"],
    }


async def failing(coroutine: Any, expected: type[BaseException]) -> BaseException:
    """Await `coroutine`, which must raise `expected`; return the error (for setup steps)."""
    try:
        await coroutine
    except expected as exc:
        return exc
    raise AssertionError(f"expected {expected.__name__}, but nothing was raised")


def apply_change(env: FakeEnv, settings: Any, kind: str) -> None:
    """Change the settings the way a test runner (or a careless caller) could after `configure()`."""
    if kind == "host":
        env.override("default", HOST="somewhere-else")
    elif kind == "options":
        env.set_options("default", {"connect_timeout": 3})
    elif kind == "engine":
        env.use_engine("default", UNSUPPORTED_ENGINE)
    elif kind == "use_tz":
        settings.USE_TZ = not settings.USE_TZ
    elif kind == "time_zone":
        settings.TIME_ZONE = "Asia/Seoul"
    elif kind == "alias_added":
        env.add_alias("late", "late_db", FAKE_ENGINE)
    else:
        raise ValueError(kind)


class ConcurrentOpenScenario:
    """Task B holds `other`'s open lock (its driver is gated); `setup([default, other, third])`
    opens `default`, parks behind B, and fails on `third` once B has opened `other`."""

    def __init__(self, env: FakeEnv, counter: LockCounter) -> None:
        self.env = env
        self.counter = counter
        self.b: asyncio.Task[Any] | None = None
        self.setup_task: asyncio.Task[None] | None = None

    async def run(self) -> list[Any]:
        from asyncopation.connection import configure
        from asyncopation.connection.lifecycle import ensure_driver, setup
        from tests.support.aio import wait_until

        script = self.env.script
        self.env.add_alias("third", "fake_third_db", FAKE_ENGINE)
        configure()  # the new alias is part of the configuration
        script.gate = asyncio.Event()
        script.gate_driver_index = 0  # B's driver is the first one built
        script.fail_open_after = 2  # the third open (after default and other) fails
        self.b = asyncio.create_task(ensure_driver("other"))
        await wait_until(lambda: _opener_started(self.env.recorder))
        self.setup_task = asyncio.create_task(setup(["default", "other", "third"]))
        await wait_until(lambda: _three_acquires(self.counter))  # setup is parked behind B
        script.gate.set()
        script.gate = None
        return list(await asyncio.gather(self.b, self.setup_task, return_exceptions=True))


class ClosedLoopState:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def closing_mark_remains(self) -> bool:
        from asyncopation.connection import registry

        return self.loop in registry._closing_loops

    def flights_remain(self) -> bool:
        from asyncopation.connection import registry

        return any(key[1] is self.loop for key in registry._flights)


async def leave_closing_and_flight_in_a_closed_loop(alias: str = "default") -> ClosedLoopState:
    """In a loop that is then closed: open `alias`, start a statement and begin closing."""
    from asyncopation.connection import registry
    from asyncopation.connection.lifecycle import ensure_driver

    async def run() -> ClosedLoopState:
        await ensure_driver(alias)
        registry.statement_started(alias)
        registry.begin_closing(asyncio.get_running_loop())
        return ClosedLoopState(asyncio.get_running_loop())

    return await run_in_thread_loop(run())


def purge_and_report_leaks() -> list[str]:
    """Purge closed loops, return (and so consume) the leaked entries' aliases."""
    from asyncopation.connection import registry

    registry.purge_closed_loops()
    return aliases_of(registry.take_leaked())
