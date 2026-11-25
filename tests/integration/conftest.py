"""Integration-wide guard: no test may leave a driver in the process-global registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.lifecycle import assert_registry_clean


@pytest.fixture(autouse=True)
def registry_left_clean() -> Iterator[None]:
    """Torn down last: records leftover entries (including pools of loops that were closed
    without `close()`) BEFORE resetting, and fails the test when there are any."""
    yield
    assert_registry_clean()
