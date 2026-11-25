"""Fixtures for the lifecycle integration tests: the real PostgreSQL engine behind a spy."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

from tests.support.lifecycle.checks import teardown_lifecycle
from tests.support.lifecycle.fakes import Recorder
from tests.support.lifecycle.postgres import (
    application_name_override,
    forget_wrapper,
    install_spy_engine,
    new_application_name,
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
    from asyncopation.connection import registry

    registry.reset()  # an earlier test's configuration or entries must not leak in
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


@pytest.fixture
def tight_spy(spy_env: Recorder, lifecycle_configured: None) -> Iterator[Recorder]:
    """`spy` with a pool of exactly one connection (a second borrower has to wait for it)."""
    from django.db import connections

    from asyncopation.connection import configure
    from tests.support.lifecycle.postgres import patched_alias_settings

    options = {
        **connections.settings["default"].get("OPTIONS", {}),
        "pool": {"min_size": 1, "max_size": 1, "timeout": 5},
    }
    with patched_alias_settings("default", OPTIONS=options):
        configure()
        yield spy_env
