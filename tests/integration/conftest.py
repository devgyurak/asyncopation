"""Integration-wide guard: no test may leave a driver in the process-global registry."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.support.lifecycle.checks import assert_registry_clean


@pytest.fixture(autouse=True)
async def registry_left_clean() -> AsyncIterator[None]:
    """Torn down last: records leftover entries (including pools of loops that were closed
    without `close()`) BEFORE resetting, and fails the test when there are any."""
    yield
    await assert_registry_clean()
