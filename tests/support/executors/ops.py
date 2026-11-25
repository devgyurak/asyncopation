"""Call helpers: operations by name, errors as values, work run in child tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any

from tests.support.aio import collect

OWNER_MESSAGE = (
    "queries inside async_atomic must run in the task that opened it; "
    "move concurrent work outside the transaction"
)

STATEMENTS = ["fetch_all", "execute", "execute_many", "stream"]
CONTROLS = [
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release_savepoint",
    "rollback_to_savepoint",
]
CONTROLS_THAT_RAISE_WHEN_BROKEN = [op for op in CONTROLS if op != "rollback"]


async def call(executor: Any, op: str) -> Any:
    """Run one operation by name: statements with a fixed SQL, controls with savepoint `sp`."""
    match op:
        case "fetch_all":
            return await executor.fetch_all("SELECT 1", None)
        case "execute":
            return await executor.execute("UPDATE t SET x = 1", None)
        case "execute_many":
            return await executor.execute_many("INSERT INTO t VALUES (%s)", [(1,), (2,)])
        case "stream":
            async with executor.stream("SELECT 1", None, 10) as chunks:
                return await collect(chunks)
        case "begin" | "commit" | "rollback":
            return await getattr(executor, op)()
        case "savepoint" | "release_savepoint" | "rollback_to_savepoint":
            return await getattr(executor, op)("sp")
    raise ValueError(op)


async def call_with(executor: Any, op: str, sql: str, params: Any) -> Any:
    """Run one statement kind with the given SQL and parameters (a stream is read in chunks of 2
    and returned as the list of chunks)."""
    match op:
        case "fetch_all":
            return await executor.fetch_all(sql, params)
        case "execute":
            return await executor.execute(sql, params)
        case "execute_many":
            return await executor.execute_many(sql, params)
        case "stream":
            async with executor.stream(sql, params, 2) as chunks:
                return await collect(chunks)
    raise ValueError(op)


async def raised_by(awaitable: Awaitable[Any]) -> BaseException | None:
    """The exception `awaitable` raises (cancellation included), `None` when it returns."""
    try:
        await awaitable
    except BaseException as exc:
        return exc
    return None


async def in_gather_child(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run `coroutine` in a task made by `asyncio.gather` (copies the caller's context)."""
    return (await asyncio.gather(coroutine))[0]


async def in_taskgroup_child(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run `coroutine` in a `TaskGroup` child; a single error is re-raised unwrapped."""
    try:
        async with asyncio.TaskGroup() as group:
            task = group.create_task(coroutine)
    except ExceptionGroup as errors:
        raise errors.exceptions[0] from None
    return task.result()


async def in_child_task(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run `coroutine` in a plain `create_task` child."""
    return await asyncio.create_task(coroutine)


async def yield_once() -> None:
    """Let every task that is ready run one step. A task made just before this call has then run up
    to its first real suspension (deterministic: no clock involved), so "it is still waiting" can
    be observed without a sleep."""
    await asyncio.sleep(0)


def kinds_of(driver: Any) -> list[str]:
    return [statement.kind for statement in driver.statements]
