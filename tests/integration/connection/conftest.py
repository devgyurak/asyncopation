"""Fixtures for the lifecycle integration tests: the real PostgreSQL engine behind a spy."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.support.lifecycle import Recorder, install_spy_engine, teardown_lifecycle


@pytest.fixture
async def spy_env(
    lifecycle_configured: None, readonly_db: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Recorder]:
    """Real engine, recorded; registry empty and NOT configured (the test calls `configure()`)."""
    recorder = Recorder()
    install_spy_engine(monkeypatch, recorder)
    yield recorder
    await teardown_lifecycle()


@pytest.fixture
async def spy(spy_env: Recorder) -> Recorder:
    """`spy_env` after `configure()` against the live (test) database settings."""
    from asyncopation.connection import configure

    configure()
    return spy_env
