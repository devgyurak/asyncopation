"""Fixtures shared by the DB-free unit tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.compile_wrappers import clear_alias, unique_alias


@pytest.fixture
def alias() -> Iterator[str]:
    """A throwaway connection alias; any wrapper left installed is removed afterwards."""
    name = unique_alias()
    yield name
    clear_alias(name)
