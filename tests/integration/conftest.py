"""Integration-wide guard: no test may leave a driver in the process-global registry."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.conftest import Differential
from tests.support.alist_env import asyncopation_runner, prepare_registry, release_drivers
from tests.support.lifecycle.checks import assert_registry_clean


@pytest.fixture(autouse=True)
async def registry_left_clean() -> AsyncIterator[None]:
    """Torn down last: records leftover entries (including pools of loops that were closed
    without `close()`) BEFORE resetting, and fails the test when there are any."""
    yield
    await assert_registry_clean()


@pytest.fixture
async def async_env(lifecycle_configured: None, readonly_db: None) -> AsyncIterator[None]:
    """A configured registry; drivers opened lazily by `aq(...)` calls are closed afterwards."""
    prepare_registry()
    yield
    await release_drivers()


@pytest.fixture
def differential(async_env: None) -> Differential:
    """`await differential(build, op="list")` - sync ORM vs Asyncopation, with driver cleanup."""
    return asyncopation_runner()
