"""Driver-contract scenario against the PostgreSQL driver (see `tests/integration/drivers`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.aio import wait_until
from tests.support.drivers.admin import admin_scalar, backend_ends, backend_exists, settled_stats
from tests.support.drivers.probes import backend_pid
from tests.support.executors.ops import raised_by

SLEEP = 30


@dataclass(frozen=True)
class CancelAndRollback:
    cancelled: bool
    rollback_error: BaseException | None
    backend_ended: bool
    old_pid: int
    fresh_pid: int


async def _backend_gone_after_borrow(driver: Any, pid: int, *, expect_gone: bool) -> bool:
    """Whether the backend is gone once the borrow ended. A discarded connection is closed in the
    background, so that case polls until it is gone; a connection that stays is looked at once the
    pool has taken it back (no fixed wait for something that must not happen)."""
    if expect_gone:
        return await backend_ends(pid)
    await settled_stats(driver, min_idle=1)
    return not await backend_exists(pid)


async def cancel_then_rollback(driver: Any, *, begin: bool) -> CancelAndRollback:
    """Cancel a slow statement of a borrow (the borrow stays open), then call `rollback()` on the
    same connection, then leave the borrow and inspect the backend."""
    marker = f"contract_{id(driver)}"
    async with driver.connection() as connection:
        pid = await backend_pid(connection)
        if begin:
            await connection.begin()
        statement = asyncio.create_task(
            connection.fetch_all(f"SELECT pg_sleep({SLEEP}) /* {marker} */", None)
        )

        async def running() -> bool:
            count = await admin_scalar(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid = %s AND query LIKE %s AND state = 'active'",
                (pid, f"%{marker}%"),
            )
            return count > 0

        await wait_until(running)
        statement.cancel()
        await asyncio.wait([statement])
        rollback_error = await raised_by(connection.rollback())
    ended = await _backend_gone_after_borrow(driver, pid, expect_gone=begin)
    async with driver.connection() as fresh:
        fresh_pid = await backend_pid(fresh)
    return CancelAndRollback(statement.cancelled(), rollback_error, ended, pid, fresh_pid)
