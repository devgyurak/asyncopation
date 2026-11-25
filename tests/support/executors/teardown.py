"""Executor scenarios about how blocks and streams end: leaks, owner rules, close ordering.
Every function only *observes* and returns an outcome; the tests assert."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import in_gather_child, kinds_of, raised_by
from tests.support.executors.scenarios import (
    ALIAS,
    SELECT,
    _enter_and_leave_pin,
    _executors,
    _registry,
)
from tests.support.executors.streams import open_streams_past_the_busy_guard


# --------------------------------------------------------------------------- pin leak paths
@dataclass(frozen=True)
class PinFailure:
    error: BaseException | None
    in_flight: int
    close_error: BaseException | None


async def pin_when_borrow_fails(env: ExecEnv) -> PinFailure:
    """`driver.connection()` raises after the pin's unit started; then `close()` is called."""
    from asyncopation.connection import close

    env.default.borrow_error = RuntimeError("pool exhausted")
    error = await raised_by(_enter_and_leave_pin())
    in_flight = _registry().in_flight(ALIAS)
    return PinFailure(error, in_flight, await raised_by(close(timeout=0.5)))


async def pin_unknown_alias() -> PinFailure:
    async def enter() -> None:
        async with _executors().pin("missing"):
            pass

    error = await raised_by(enter())
    return PinFailure(error, _registry().in_flight("missing"), None)


@dataclass(frozen=True)
class FailedBlock:
    error: BaseException | None
    executor_closed: bool
    current_is_pinned: bool
    released: list[bool]
    in_flight: int


async def pin_block_raises(env: ExecEnv) -> FailedBlock:
    """An ordinary exception leaves the pin block."""
    seen: list[Any] = []

    async def block() -> None:
        async with _executors().pin(ALIAS) as executor:
            seen.append(executor)
            raise ValueError("boom")

    error = await raised_by(block())
    return FailedBlock(
        error,
        seen[0].closed,
        _executors().current_executor(ALIAS).pinned,
        env.default.released,
        _registry().in_flight(ALIAS),
    )


# --------------------------------------------------------------------------- entering streams
async def unentered_stream_then_statement(env: ExecEnv) -> list[Any]:
    """A stream is created but never entered; the pin then runs a statement."""
    async with _executors().pin(ALIAS) as executor:
        executor.stream(SELECT, None, 1)
        return await executor.fetch_all(SELECT, None)


async def enter_stream_in_child() -> BaseException | None:
    async with _executors().pin(ALIAS) as executor:
        manager = executor.stream(SELECT, None, 1)
        return await raised_by(in_gather_child(manager.__aenter__()))


async def enter_stream_after_block() -> BaseException | None:
    async with _executors().pin(ALIAS) as executor:
        manager = executor.stream(SELECT, None, 1)
    return await raised_by(manager.__aenter__())


# --------------------------------------------------------------------------- rollback and streams
@dataclass(frozen=True)
class RollbackWithStream:
    error: BaseException | None
    streams_open: int
    kinds: list[str]
    read_error: BaseException | None


async def rollback_with_open_stream(env: ExecEnv) -> RollbackWithStream:
    """`rollback()` while a stream of the transaction is still open."""
    env.default.queue([(1,), (2,), (3,)])
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        chunks = await executor.stream(SELECT, None, 1).__aenter__()
        await anext(chunks)
        error = await raised_by(executor.rollback())
        return RollbackWithStream(
            error,
            env.default.streams_open,
            kinds_of(env.default),
            await raised_by(anext(chunks)),
        )


@dataclass(frozen=True)
class SlowClose:
    second_close_done: bool
    released_meanwhile: list[bool]
    released_after: list[bool]
    errors: list[BaseException | None]


async def close_stream_slowly(env: ExecEnv) -> SlowClose:
    """A stream close is held at the driver; a second `close()` and the pin exit (this task) must
    wait for it. A looker task, which runs once this task blocks, records what happened so far and
    lets the held close finish."""
    driver = env.default
    hold = driver.hold_stream_closes()
    driver.queue([(1,), (2,)])
    pin_cm = _executors().pin(ALIAS)
    executor = await pin_cm.__aenter__()
    manager = executor.stream(SELECT, None, 1)
    await manager.__aenter__()
    first = asyncio.create_task(manager.close())
    await hold.wait_entered()
    second = asyncio.create_task(manager.close())
    snapshot: list[Any] = []

    async def look_then_release() -> None:
        snapshot.extend([second.done(), list(driver.released)])
        hold.release()

    looker = asyncio.create_task(look_then_release())
    errors = [await raised_by(pin_cm.__aexit__(None, None, None))]  # blocks until the release
    errors.extend([await raised_by(first), await raised_by(second), await raised_by(looker)])
    return SlowClose(snapshot[0], snapshot[1], list(driver.released), errors)


@dataclass(frozen=True)
class TwoStreamsAtExit:
    exit_error: BaseException | None
    streams_open: int
    released: list[bool]
    in_flight: int


async def exit_pin_with_two_open_streams(env: ExecEnv) -> TwoStreamsAtExit:
    """Leave a pin with two open streams whose first close fails.

    Two streams are opened past the busy guard to reach the defence in depth of the pin exit.
    """
    driver = env.default
    driver.queue([(1,), (2,)], [(3,), (4,)])
    driver.fail_next("stream_close", ValueError("close failed"))
    pin_cm = _executors().pin(ALIAS)
    executor = await pin_cm.__aenter__()
    await open_streams_past_the_busy_guard(executor, 2)
    error = await raised_by(pin_cm.__aexit__(None, None, None))
    return TwoStreamsAtExit(
        error, driver.streams_open, driver.released, _registry().in_flight(ALIAS)
    )


# --------------------------------------------------------------------------- unusual exits
@dataclass(frozen=True)
class ForeignExit:
    error: BaseException | None
    executor_closed: bool
    in_flight: int
    released: list[bool]


async def leave_a_pin_from_another_task(env: ExecEnv) -> ForeignExit:
    """The block is left by a task that is not the one that entered it (a finalizer)."""
    pin_cm = _executors().pin(ALIAS)
    executor = await pin_cm.__aenter__()
    error = await raised_by(in_gather_child(pin_cm.__aexit__(None, None, None)))
    return ForeignExit(error, executor.closed, _registry().in_flight(ALIAS), env.default.released)


async def depth_after_a_nested_pin_raises() -> int:
    """The depth of the outer pin after an inner pin of the same task ended with an exception."""

    async def inner() -> None:
        async with _executors().pin(ALIAS):
            raise ValueError("boom")

    async with _executors().pin(ALIAS) as executor:
        await raised_by(inner())
        return executor.depth


async def stream_of_a_closed_pin() -> Any:
    """`stream()` called on an executor whose block ended (no `await`, no `async with`)."""
    async with _executors().pin(ALIAS) as executor:
        pass
    return executor.stream(SELECT, None, 1)
