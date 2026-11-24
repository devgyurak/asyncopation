"""Fixtures for PostgreSQL driver tests: opened drivers with pool shapes, scratch table."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.support.drivers import (
    Binding,
    create_scratch_table,
    drop_scratch_table,
    open_driver,
)


@pytest.fixture(
    params=[
        pytest.param(Binding({}, False), id="client-binding"),
        pytest.param(Binding({"server_side_binding": True}, True), id="server-binding"),
    ]
)
def binding(request: pytest.FixtureRequest) -> Binding:
    return request.param


@pytest.fixture
async def driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    async with open_driver(options=binding.options, pool={"min_size": 1, "max_size": 4}) as d:
        yield d


@pytest.fixture
async def single_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    async with open_driver(
        options=binding.options, pool={"min_size": 1, "max_size": 1, "timeout": 10}
    ) as d:
        yield d


@pytest.fixture
async def elastic_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    async with open_driver(
        options=binding.options, pool={"min_size": 0, "max_size": 2, "timeout": 10}
    ) as d:
        yield d


@pytest.fixture
async def counted_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    async with open_driver(
        options=binding.options, pool={"min_size": 2, "max_size": 2, "timeout": 10}
    ) as d:
        yield d


@pytest.fixture
async def tight_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    async with open_driver(
        options=binding.options, pool={"min_size": 1, "max_size": 1, "timeout": 0.2}
    ) as d:
        yield d


@pytest.fixture
async def no_cursor_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    """DISABLE_SERVER_SIDE_CURSORS = True (poolers in transaction mode)."""
    async with open_driver(
        options=binding.options,
        pool={"min_size": 1, "max_size": 2},
        overrides={"DISABLE_SERVER_SIDE_CURSORS": True},
    ) as d:
        yield d


@pytest.fixture
async def idle_timeout_driver(django_db_setup: None, binding: Binding) -> AsyncIterator[Any]:
    """The server ends sessions idle in a transaction after 300 ms (a FATAL error)."""
    options = {**binding.options, "options": "-c idle_in_transaction_session_timeout=300ms"}
    async with open_driver(options=options, pool={"min_size": 0, "max_size": 2}) as d:
        yield d


@pytest.fixture
async def scratch(django_db_setup: None) -> AsyncIterator[str]:
    table = await create_scratch_table()
    yield table
    await drop_scratch_table()
