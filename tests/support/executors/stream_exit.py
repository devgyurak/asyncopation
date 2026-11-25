"""Executor scenarios about streams whose close fails and streams that are entered twice.
Every function only *observes* and returns an outcome; the tests assert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, SELECT, _executors, _registry


@dataclass(frozen=True)
class RollbackAfterFailedClose:
    error: BaseException | None
    kinds: list[str]
    streams_open: int


async def rollback_when_stream_close_fails(
    env: ExecEnv, fault: BaseException | None = None
) -> RollbackAfterFailedClose:
    """`rollback()` with a stream open whose close fails (`ValueError` unless `fault`): is the
    ROLLBACK still sent?"""
    driver = env.default
    driver.queue([(1,), (2,)])
    driver.fail_next("stream_close", fault or ValueError("close failed"))
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        await executor.stream(SELECT, None, 1).__aenter__()
        error = await raised_by(executor.rollback())
        return RollbackAfterFailedClose(error, kinds_of(driver), driver.streams_open)


async def exit_pin_with_close_faults(env: ExecEnv, *faults: BaseException) -> BaseException | None:
    """Leave a pin with one open stream per fault, each close failing with its fault in turn.

    The busy guard is lifted to open several streams (see `exit_pin_with_two_open_streams`).
    """
    driver = env.default
    driver.queue(*[[(1,), (2,)] for _ in faults])
    for fault in faults:
        driver.fail_next("stream_close", fault)
    pin_cm = _executors().pin(ALIAS)
    executor = await pin_cm.__aenter__()
    for _ in faults:
        executor._busy = False
        await executor.stream(SELECT, None, 1).__aenter__()
    return await raised_by(pin_cm.__aexit__(None, None, None))


async def close_error_kind(env: ExecEnv) -> str:
    """Name of the exception raised when one stream close fails with an ordinary error and
    another with a cancellation."""
    error = await exit_pin_with_close_faults(
        env, ValueError("close failed"), asyncio.CancelledError()
    )
    return type(error).__name__


@dataclass(frozen=True)
class PinExit:
    error: BaseException | None
    executor_closed: bool
    in_flight: int
    released: list[bool]


async def pin_exit_with_failing_stream_close(env: ExecEnv, *, body_raises: bool) -> PinExit:
    """The block leaves a stream open, its close fails during the exit; the body either raises
    `ValueError("boom")` or ends cleanly."""
    driver = env.default
    driver.queue([(1,), (2,)])
    driver.fail_next("stream_close", RuntimeError("close failed"))
    seen: list[Any] = []

    async def block() -> None:
        async with _executors().pin(ALIAS) as executor:
            seen.append(executor)
            await executor.stream(SELECT, None, 1).__aenter__()
            if body_raises:
                raise ValueError("boom")

    error = await raised_by(block())
    return PinExit(error, seen[0].closed, _registry().in_flight(ALIAS), driver.released)


@dataclass(frozen=True)
class SecondEntry:
    error: BaseException | None
    rows_after: list[Any]


async def enter_stream_twice(env: ExecEnv) -> SecondEntry:
    """A stream is entered, entered again, then closed; a statement follows."""
    env.default.queue([(1,), (2,)])
    async with _executors().pin(ALIAS) as executor:
        stream = executor.stream(SELECT, None, 1)
        await stream.__aenter__()
        error = await raised_by(stream.__aenter__())
        await stream.close()
        return SecondEntry(error, await executor.fetch_all(SELECT, None))


async def enter_stream_after_close(env: ExecEnv) -> SecondEntry:
    """A stream is entered, closed and entered again; a statement follows."""
    env.default.queue([(1,), (2,)])
    async with _executors().pin(ALIAS) as executor:
        stream = executor.stream(SELECT, None, 1)
        await stream.__aenter__()
        await stream.close()
        error = await raised_by(stream.__aenter__())
        return SecondEntry(error, await executor.fetch_all(SELECT, None))


async def enter_stream_whose_open_fails(env: ExecEnv) -> SecondEntry:
    """The driver fails to open the stream; a statement follows on the same pin."""
    env.default.queue(ValueError("open failed"))
    async with _executors().pin(ALIAS) as executor:
        error = await raised_by(executor.stream(SELECT, None, 1).__aenter__())
        return SecondEntry(error, await executor.fetch_all(SELECT, None))


async def rollback_when_the_stream_close_is_cancelled(env: ExecEnv) -> RollbackAfterFailedClose:
    """A cancelled stream close inside a transaction breaks it: `rollback()` must raise that
    cancellation (not `TransactionManagementError`) and send no ROLLBACK."""
    return await rollback_when_stream_close_fails(env, asyncio.CancelledError())
