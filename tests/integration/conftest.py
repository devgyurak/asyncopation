"""Integration-wide guard: no test may leave a driver in the process-global registry."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from harness.catalog.models import Author, Tag
from tests.conftest import Differential
from tests.support.alist_env import asyncopation_runner, prepare_registry, release_drivers
from tests.support.lifecycle.checks import assert_registry_clean
from tests.support.seeded import (
    seeded_author_with_books,
    seeded_book_pk,
    seeded_edition_keys,
    seeded_tag_with_peer,
)


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


@pytest.fixture
async def edition_keys(async_env: None) -> list[tuple[int, int]]:
    """Composite primary keys of seeded editions, read from the database."""
    return await seeded_edition_keys()


@pytest.fixture
async def author_with_books(async_env: None) -> Author:
    """A seeded author that has books, read from the database."""
    return await seeded_author_with_books()


@pytest.fixture
async def tag_with_peer(async_env: None) -> tuple[Tag, str]:
    """A seeded tag and the name of another tag on one of its books, read from the database."""
    return await seeded_tag_with_peer()


@pytest.fixture
async def book_pk(async_env: None) -> int:
    """The primary key of the first seeded book, read from the database."""
    return await seeded_book_pk()
