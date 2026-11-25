"""Fixtures for the lifecycle unit tests: a fake engine, fake DATABASES and a clean registry."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from asyncopation.connection import registry
from tests.support.lifecycle import (
    ClosingScenario,
    FakeEnv,
    OpeningScenario,
    OverlappingClose,
    ThreadLoop,
    dispose_environment,
    drain_in_flight,
    hold_driver_in_live_loop,
    install_fake_environment,
    leave_open_in_other_loop,
    release_live_loop,
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
