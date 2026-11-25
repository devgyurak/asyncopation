"""Executor scenarios about failures: which errors break a transaction, what statements forward,
how streams fail. Every function only *observes* and returns an outcome; the tests assert."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from asyncopation.drivers.base import ExecResult
from tests.support.executors.fakes import ExecEnv, LostConnection
from tests.support.executors.ops import call, call_with, raised_by
from tests.support.executors.scenarios import ALIAS, SELECT, _executors, executor_of

FORWARD_SQL = "SELECT %s"
FORWARD_PARAMS: dict[str, Any] = {
    "fetch_all": (5,),
    "execute": (5,),
    "execute_many": [(5,), (6,)],
    "stream": (5,),
}
FORWARD_RESULTS: dict[str, Any] = {
    "fetch_all": [(1,), (2,)],
    "execute": ExecResult(rowcount=3),
    "execute_many": ExecResult(rowcount=5),
    "stream": [(1,), (2,), (3,)],
}


# --------------------------------------------------------------------------- forwarding
@dataclass(frozen=True)
class Forwarded:
    result: Any
    statement: tuple[Any, ...]


async def forward_statement(env: ExecEnv, *, pinned: bool, op: str) -> Forwarded:
    """One statement through a pinned or borrowed executor: its result and what the driver saw."""
    env.default.queue(FORWARD_RESULTS[op])
    async with executor_of(pinned) as executor:
        result = await call_with(executor, op, FORWARD_SQL, FORWARD_PARAMS[op])
    return Forwarded(result, tuple(env.default.statements[-1]))


# --------------------------------------------------------------------------- what breaks a pin
async def error_in_transaction(env: ExecEnv, op: str, error: BaseException) -> bool:
    """`op` raises `error` inside an open transaction: is the pin broken afterwards?"""
    env.default.queue(error)
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        await raised_by(call(executor, op))
        return executor.broken


async def disconnect_in_statement(env: ExecEnv, *, begin: bool) -> bool:
    """A statement fails with an error the driver classifies as a lost connection."""
    env.default.disconnect_errors = (LostConnection,)
    env.default.queue(LostConnection())
    async with _executors().pin(ALIAS) as executor:
        if begin:
            await executor.begin()
        await raised_by(executor.fetch_all(SELECT, None))
        return executor.broken


async def _step(env: ExecEnv, executor: Any, step: str) -> None:
    driver = env.default
    match step:
        case "begin" | "commit" | "rollback":
            await getattr(executor, step)()
        case "cancel":
            driver.queue(asyncio.CancelledError())
            await raised_by(executor.fetch_all(SELECT, None))
        case "begin_fails":
            driver.fail_next("begin", ValueError("begin failed"))
            await raised_by(executor.begin())
        case "begin_cancelled":
            driver.fail_next("begin", asyncio.CancelledError())
            await raised_by(executor.begin())
        case "commit_fails":
            driver.queue(ValueError("commit failed"))
            await raised_by(executor.commit())
        case _:
            raise ValueError(step)


async def broken_after(env: ExecEnv, steps: Sequence[str]) -> bool:
    """Run `steps` (`begin`, `commit`, `rollback`, `cancel` = a cancelled statement, `begin_fails`,
    `begin_cancelled`, `commit_fails`) on one pin and say whether it ended up broken."""
    async with _executors().pin(ALIAS) as executor:
        for step in steps:
            await _step(env, executor, step)
        return executor.broken


# --------------------------------------------------------------------------- stream failures
@dataclass(frozen=True)
class StreamFailure:
    error: BaseException | None
    broken: bool


async def _failing_chunk_read(executor: Any) -> tuple[BaseException | None, bool]:
    async with executor.stream(SELECT, None, 1) as chunks:
        error = await raised_by(anext(chunks))
        return error, executor.broken


async def _cancelled_body(executor: Any) -> None:
    async with executor.stream(SELECT, None, 1):
        raise asyncio.CancelledError


async def stream_fails(env: ExecEnv, where: str, *, begin: bool) -> StreamFailure:
    """A pinned stream is cancelled while reading a chunk (`chunk`), in its body (`body`) or while
    it closes (`close`). `broken` is read right after the failure surfaced."""
    driver = env.default
    driver.queue([(1,), (2,), (3,)])
    async with _executors().pin(ALIAS) as executor:
        if begin:
            await executor.begin()
        match where:
            case "chunk":
                driver.fail_next("chunk", asyncio.CancelledError())
                error, broken = await _failing_chunk_read(executor)
                return StreamFailure(error, broken)
            case "body":
                error = await raised_by(_cancelled_body(executor))
            case "close":
                driver.fail_next("stream_close", asyncio.CancelledError())
                error = await raised_by(call(executor, "stream"))
            case _:
                raise ValueError(where)
        return StreamFailure(error, executor.broken)
