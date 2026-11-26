"""Stream scenarios under observers: every way a stream block can end (ADR-0005 Decision 3).

Each scenario queues 25 rows, reads them in chunks of 10 and returns an `Observed`; `how` names
the way the block ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from tests.support.aio import collect
from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import in_gather_child
from tests.support.executors.scenarios import _executors, executor_of
from tests.support.hooks.events import Recorder, hooks
from tests.support.hooks.scenarios import Observed, _outcome, registered

SQL = "SELECT g FROM t"
CHUNK = 10
ROWS: list[tuple[Any, ...]] = [(n,) for n in range(1, 26)]


class ConsumerError(Exception):
    """Raised by the consumer's own code inside the stream block."""


async def _read(env: ExecEnv, how: str, *, pinned: bool) -> tuple[Any, BaseException | None]:
    """Open the stream on a fresh executor and leave it the way `how` says."""
    async with executor_of(pinned) as executor:
        if how == "generator_close":
            return await _outcome(_close_generator_early(executor))
        return await _outcome(_use(executor, env, how))


async def _use(executor: Any, env: ExecEnv, how: str) -> Any:
    async with executor.stream(SQL, None, CHUNK) as chunks:
        if how == "full":
            return await collect(chunks)
        if how == "break":
            return await _first_by_break(chunks)
        first = await anext(chunks)
        if how == "consumer_error":
            raise ConsumerError("the consumer failed")
        return first  # "one_chunk", "close_error"


async def _first_by_break(chunks: Any) -> Any:
    """Leave an `async for` over the chunks with `break` after the first one."""
    first = None
    async for chunk in chunks:
        first = chunk
        break
    return first


async def _rows_of(executor: Any) -> AsyncGenerator[list[Any]]:
    async with executor.stream(SQL, None, CHUNK) as chunks:
        async for chunk in chunks:
            yield chunk


async def _close_generator_early(executor: Any) -> Any:
    """An async generator over the stream, closed after one chunk (`GeneratorExit` in the block)."""
    generator = _rows_of(executor)
    first = await anext(generator)
    await generator.aclose()
    return first


async def stream_left(env: ExecEnv, how: str, *, pinned: bool) -> Observed:
    """`how`: `full`, `one_chunk`, `break`, `consumer_error`, `generator_close`, `chunk_error`,
    `close_error` or `chunk_cancelled` (the driver's read raises `CancelledError`)."""
    env.default.queue(list(ROWS))
    injected: BaseException | None = None
    if how == "chunk_error":
        injected = ValueError("chunk read failed")
        env.default.fail_next("chunk", injected)
    elif how == "close_error":
        injected = ValueError("stream close failed")
        env.default.fail_next("stream_close", injected)
    elif how == "chunk_cancelled":
        injected = asyncio.CancelledError()
        env.default.fail_next("chunk", injected)
    recorder = Recorder()
    with hooks().observe(recorder):
        result, error = await _read(env, how, pinned=pinned)
    return Observed(recorder, result, error, injected)


async def cancel_while_reading(env: ExecEnv, *, pinned: bool) -> Observed:
    """The task reading a stream is cancelled while it waits for a chunk."""
    env.default.queue(list(ROWS))
    hold = env.default.hold_chunks()
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor, executor.stream(SQL, None, CHUNK) as chunks:
            return await anext(chunks)

    with hooks().observe(recorder):
        task = asyncio.create_task(run())
        await hold.wait_entered()
        task.cancel()
        await asyncio.wait([task])
    return Observed(recorder, cancelled=task.cancelled())


async def cancel_between_chunks(env: ExecEnv, *, pinned: bool) -> Observed:
    """The consumer reads one chunk, then waits on an event inside the stream block and its task
    is cancelled there (a cancellation of the block, not of a chunk read)."""
    env.default.queue(list(ROWS))
    recorder = Recorder()
    waiting = asyncio.Event()

    async def run() -> Any:
        async with executor_of(pinned) as executor, executor.stream(SQL, None, CHUNK) as chunks:
            await anext(chunks)
            waiting.set()
            await asyncio.Event().wait()

    with hooks().observe(recorder):
        task = asyncio.create_task(run())
        await waiting.wait()
        task.cancel()
        await asyncio.wait([task])
    return Observed(recorder, cancelled=task.cancelled())


# --------------------------------------------------------------------------- snapshots
async def observer_added_while_open(
    env: ExecEnv, *, scope: str, pinned: bool
) -> tuple[Recorder, Recorder]:
    """A stream opened under `first`; `late` (scoped or global) is added before it closes.
    Returns `(first, late)`."""
    env.default.queue(list(ROWS))
    first, late = Recorder(), Recorder()
    with hooks().observe(first):
        async with executor_of(pinned) as executor:
            manager = executor.stream(SQL, None, CHUNK)
            chunks = await manager.__aenter__()
            await anext(chunks)
            if scope == "global":
                with registered(late):
                    await manager.__aexit__(None, None, None)
            else:
                with hooks().observe(late):
                    await manager.__aexit__(None, None, None)
    return first, late


async def closed_after_the_block(env: ExecEnv, *, pinned: bool, in_child: bool) -> Recorder:
    """A stream opened inside `observe()` and closed after the block exited (by its own task or
    by another one, like an async-generator finalizer)."""
    env.default.queue(list(ROWS))
    recorder = Recorder()
    async with executor_of(pinned) as executor:
        with hooks().observe(recorder):
            manager = executor.stream(SQL, None, CHUNK)
            chunks = await manager.__aenter__()
            await anext(chunks)
        closing = manager.__aexit__(None, None, None)
        if in_child:
            await in_gather_child(closing)
        else:
            await closing
    return recorder


async def borrowed_stream_manager_without_subscribers(env: ExecEnv) -> list[Any]:
    """A borrowed stream read in full with no observer at all (the plain path still works)."""
    env.default.queue(list(ROWS))
    async with _executors().BorrowedExecutor("default").stream(SQL, None, CHUNK) as chunks:
        return await collect(chunks)
