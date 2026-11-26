"""Fixtures for MySQL/MariaDB driver tests: opened drivers with pool shapes, scratch tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.support.mysql_binary import create_binary_table, drop_binary_table
from tests.support.mysql_env import (
    create_scratch_table,
    drop_scratch_table,
    open_my_driver,
)
from tests.support.mysql_values import create_typed_table, drop_typed_table


@pytest.fixture
async def driver(django_db_setup: None) -> AsyncIterator[Any]:
    async with open_my_driver(pool={"min_size": 1, "max_size": 4}) as d:
        yield d


@pytest.fixture
async def single_driver(django_db_setup: None) -> AsyncIterator[Any]:
    async with open_my_driver(pool={"min_size": 1, "max_size": 1, "timeout": 10}) as d:
        yield d


@pytest.fixture
async def elastic_driver(django_db_setup: None) -> AsyncIterator[Any]:
    async with open_my_driver(pool={"min_size": 0, "max_size": 2, "timeout": 10}) as d:
        yield d


@pytest.fixture
async def counted_driver(django_db_setup: None) -> AsyncIterator[Any]:
    async with open_my_driver(pool={"min_size": 2, "max_size": 2, "timeout": 10}) as d:
        yield d


@pytest.fixture
async def tight_driver(django_db_setup: None) -> AsyncIterator[Any]:
    """One connection and an acquire timeout of 200 ms."""
    async with open_my_driver(pool={"min_size": 1, "max_size": 1, "timeout": 0.2}) as d:
        yield d


@pytest.fixture
async def bounded_queue_driver(django_db_setup: None) -> AsyncIterator[Any]:
    """One connection, at most one waiting borrower, a long acquire timeout."""
    pool = {"min_size": 1, "max_size": 1, "timeout": 10, "max_waiting": 1}
    async with open_my_driver(pool=pool) as d:
        yield d


@pytest.fixture
async def no_cursor_driver(django_db_setup: None) -> AsyncIterator[Any]:
    """DISABLE_SERVER_SIDE_CURSORS = True: streams are chunked from a buffered cursor."""
    async with open_my_driver(
        pool={"min_size": 1, "max_size": 2}, overrides={"DISABLE_SERVER_SIDE_CURSORS": True}
    ) as d:
        yield d


@pytest.fixture
async def serializable_driver(django_db_setup: None) -> AsyncIterator[Any]:
    async with open_my_driver(
        options={"isolation_level": "serializable"}, pool={"min_size": 1, "max_size": 1}
    ) as d:
        yield d


@pytest.fixture
async def scratch(django_db_setup: None) -> AsyncIterator[str]:
    table = await create_scratch_table()
    yield table
    await drop_scratch_table()


@pytest.fixture
async def typed(readonly_db: None) -> AsyncIterator[str]:
    """The typed table (DATETIME, DECIMAL, BIT, ... including NULLs and zero dates).

    Depends on `readonly_db` because the comparison side uses Django's sync connection."""
    table = await create_typed_table()
    yield table
    await drop_typed_table()


@pytest.fixture
async def binary_table(readonly_db: None) -> AsyncIterator[str]:
    """A table with a BLOB and a VARBINARY column (`readonly_db`: mysqlclient compares)."""
    table = await create_binary_table()
    yield table
    await drop_binary_table()


@pytest.fixture
async def signal_procedure(readonly_db: None) -> AsyncIterator[str]:
    """A stored procedure that raises any errno (`SIGNAL ... MYSQL_ERRNO`)."""
    from tests.support.mysql_errno import create_signal_procedure, drop_signal_procedure

    name = await create_signal_procedure()
    yield name
    await drop_signal_procedure()
