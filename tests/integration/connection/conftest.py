"""Fixtures for the lifecycle integration tests: the real PostgreSQL engine behind a spy."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.support.lifecycle import (
    Recorder,
    application_name_override,
    forget_wrapper,
    install_spy_engine,
    new_application_name,
    teardown_lifecycle,
)


@pytest.fixture
def app_name() -> str:
    """A unique `application_name`: the sessions of one test are countable in pg_stat_activity."""
    return new_application_name()


@pytest.fixture
async def spy_env(
    lifecycle_configured: None,
    readonly_db: None,
    monkeypatch: pytest.MonkeyPatch,
    app_name: str,
) -> AsyncIterator[Recorder]:
    """Real engine, recorded; sessions tagged with `app_name`; registry empty and NOT configured
    (the test calls `configure()`)."""
    recorder = Recorder()
    install_spy_engine(monkeypatch, recorder)
    forget_wrapper("default")
    with application_name_override(app_name):
        yield recorder
        await teardown_lifecycle()
    forget_wrapper("default")


@pytest.fixture
def spy(spy_env: Recorder) -> Recorder:
    """`spy_env` after `configure()` against the live (test) database settings."""
    from asyncopation.connection import configure

    configure()
    return spy_env
