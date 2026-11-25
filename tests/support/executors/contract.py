"""Driver-contract scenarios on the `RecordingDriver` (ADR-0003: a cancelled statement discards)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.executors.ops import call, raised_by


@dataclass(frozen=True)
class CancelContract:
    cancelled: BaseException | None
    rollback_error: BaseException | None
    kinds: list[str]
    connection_discarded: bool
    driver_discarded: int


async def cancel_statement_then_rollback(driver: Any, *, begin: bool) -> CancelContract:
    """The driver raises `CancelledError` for a statement (inside a transaction when `begin`);
    the caller then calls `rollback()` on the same connection and leaves the borrow."""
    driver.queue(asyncio.CancelledError())
    async with driver.connection() as connection:
        if begin:
            await connection.begin()
        cancelled = await raised_by(connection.fetch_all("SELECT 1", None))
        rollback_error = await raised_by(connection.rollback())
    discarded = connection.discarded
    return CancelContract(
        cancelled,
        rollback_error,
        [statement.kind for statement in driver.statements],
        discarded,
        driver.discarded,
    )


async def use_after_discard(driver: Any, op: str) -> BaseException | None:
    """Discard a connection (a cancelled statement in a transaction), then run `op` on it."""
    driver.queue(asyncio.CancelledError())
    async with driver.connection() as connection:
        await connection.begin()
        await raised_by(connection.fetch_all("SELECT 1", None))
        return await raised_by(call(connection, op))
