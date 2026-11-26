"""Fixtures for the lifecycle unit tests: a fake engine, fake DATABASES and a clean registry."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from asyncopation.connection import registry
from tests.support.executors.fakes import (
    ExecEnv,
    finish_executor_test,
    install_executor_environment,
)
from tests.support.hooks.events import global_observers_left
from tests.support.lifecycle.checks import dispose_environment, drain_in_flight
from tests.support.lifecycle.fakes import FakeEnv, install_fake_environment
from tests.support.lifecycle.loops import (
    ThreadLoop,
    hold_driver_in_live_loop,
    leave_open_in_other_loop,
    release_live_loop,
)
from tests.support.lifecycle.scenarios import (
    ClosingScenario,
    ConcurrentOpenScenario,
    OpeningScenario,
    OverlappingClose,
    RollbackScenario,
    count_locks,
)


@pytest.fixture
async def fake(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeEnv]:
    """Fake engine + DATABASES (aliases `default`, `other`); registry empty and unconfigured."""
    env = install_fake_environment(monkeypatch)
    yield env
    await dispose_environment()


@pytest.fixture
def configured(fake: FakeEnv) -> FakeEnv:
    from asyncopation.connection import configure

    configure()
    return fake


@pytest.fixture
async def opened(configured: FakeEnv) -> FakeEnv:
    from asyncopation.connection import setup

    await setup(["default"])
    return configured


@pytest.fixture
async def flights(opened: FakeEnv) -> AsyncIterator[FakeEnv]:
    """`opened`, and every statement the test left in flight is finished at teardown."""
    yield opened
    drain_in_flight("default")


@pytest.fixture
async def closing(opened: FakeEnv) -> AsyncIterator[ClosingScenario]:
    scenario = ClosingScenario("default", timeout=5.0)
    yield scenario
    await scenario.cleanup()


@pytest.fixture
async def opening(configured: FakeEnv) -> AsyncIterator[OpeningScenario]:
    scenario = OpeningScenario(configured)
    yield scenario
    await scenario.cleanup()


@pytest.fixture
async def parked_opening(
    configured: FakeEnv, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[OpeningScenario]:
    """An opening scenario whose lock acquisitions are counted (a queued waiter shows up)."""
    scenario = OpeningScenario(configured, counter=count_locks(monkeypatch))
    yield scenario
    await scenario.cleanup()


@pytest.fixture
def concurrent_open(configured: FakeEnv, monkeypatch: pytest.MonkeyPatch) -> ConcurrentOpenScenario:
    return ConcurrentOpenScenario(configured, count_locks(monkeypatch))


@pytest.fixture
async def rollback(
    configured: FakeEnv, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RollbackScenario]:
    scenario = RollbackScenario(configured, count_locks(monkeypatch))
    yield scenario
    await scenario.cleanup()


@pytest.fixture
async def foreign_driver(configured: FakeEnv) -> AsyncIterator[None]:
    """A driver held open by another, still running event loop (closed again at teardown)."""
    thread_loop = ThreadLoop().start()
    await hold_driver_in_live_loop(thread_loop)
    yield
    await release_live_loop(thread_loop)


@pytest.fixture
async def leaked_driver(configured: FakeEnv) -> AsyncIterator[None]:
    """A driver whose event loop was closed without `close()` (dropped with `reset()`)."""
    await leave_open_in_other_loop("default")
    yield
    registry.reset()


@pytest.fixture
async def overlapping(opened: FakeEnv) -> AsyncIterator[OverlappingClose]:
    scenario = OverlappingClose("default")
    yield scenario
    await scenario.finish()


@pytest.fixture
async def short_closing(opened: FakeEnv) -> AsyncIterator[ClosingScenario]:
    scenario = ClosingScenario("default", timeout=0.05)
    yield scenario
    await scenario.cleanup()


@pytest.fixture
async def execenv(
    fake: FakeEnv, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncIterator[ExecEnv]:
    """Aliases `default` and `other` served by gated `RecordingDriver`s; configured, not opened.
    At teardown nothing may be in flight, unless the test asks for `leaves_in_flight`."""
    env = install_executor_environment(fake, monkeypatch)
    yield env
    finish_executor_test(env, leaves_in_flight="leaves_in_flight" in request.fixturenames)


@pytest.fixture
def leaves_in_flight() -> None:
    """Opt-out of the teardown check of `execenv`: the test leaves a unit in flight on purpose
    (a `close()` waiting for it, a held statement); it is finished at teardown."""


@pytest.fixture
async def held_close(execenv: ExecEnv) -> AsyncIterator[ClosingScenario]:
    """`default` open, with one unit in flight and a `close()` waiting for it (not yet finished)."""
    from asyncopation.connection import setup

    await setup(["default"])
    scenario = ClosingScenario("default", timeout=5.0)
    await scenario.begin()
    yield scenario
    await scenario.cleanup()


@pytest.fixture(autouse=True)
def no_global_observer_left() -> Iterator[None]:
    """A test must remove every global observer it added (ADR-0005); leftovers are cleared and
    fail the test, so they cannot leak into later tests."""
    yield
    left = global_observers_left()
    assert left == (), f"global observers left registered by the test: {left}"
