"""Fixtures for the lifecycle unit tests: a fake engine, fake DATABASES and a clean registry."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from asyncopation.connection import registry
from tests.support.lifecycle import (
    ClosingScenario,
    FakeEnv,
    OpeningScenario,
    dispose_environment,
    drain_in_flight,
    install_fake_environment,
    leave_open_in_other_loop,
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
    """A driver held open by ANOTHER event loop (dropped with `reset()` at teardown)."""
    await leave_open_in_other_loop("default")
    yield
    registry.reset()


@pytest.fixture
async def short_closing(opened: FakeEnv) -> AsyncIterator[ClosingScenario]:
    scenario = ClosingScenario("default", timeout=0.05)
    yield scenario
    await scenario.cleanup()
