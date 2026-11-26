"""Executor scenarios under interceptors (ADR-0005 Decision 4), on the gated recording drivers
(`execenv`).

Every function only *observes* and returns an outcome; the tests assert. Global interceptors added
here are always removed again, even when the scenario fails.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv, LostConnection
from tests.support.executors.ops import call, in_gather_child, kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, executor_module, executor_of
from tests.support.hooks.clock import QUERY, FakeClock, with_held_borrow
from tests.support.hooks.events import Recorder, Tagged, hooks
from tests.support.hooks.interceptors import (
    Advancing,
    PassThrough,
    TaggedInterceptor,
    Waiting,
    interceptor_registered,
    supplied_result,
)
from tests.support.hooks.scenarios import Observed, outcome_of, unobserved_fetch
from tests.support.hooks.streams import CHUNK, ROWS, SQL
from tests.support.hooks.transactions import Seen

INTERCEPTOR = 5.0
"""Seconds of the fake clock an interceptor spends (timing scenarios)."""


# --------------------------------------------------------------------------- one call
async def intercepted_call(op: str, interceptor: Any, *, pinned: bool) -> Observed:
    """`op` (`ops.call`) on a pinned or borrowed executor under `intercept(interceptor)`, observed
    by a recorder."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    recorder = Recorder()
    with hooks().observe(recorder), hooks().intercept(interceptor):
        result, error = await outcome_of(run())
    return Observed(recorder, result, error)


async def intercepted_with_observer(
    op: str, interceptor: Any, observer: Any, *, pinned: bool
) -> Observed:
    """`op` under `observe(observer)` and `intercept(interceptor)` (in that order); the recorder
    of the result is empty."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(observer), hooks().intercept(interceptor):
        result, error = await outcome_of(run())
    return Observed(Recorder(), result, error)


async def only_intercepted_call(op: str, interceptor: Any, *, pinned: bool) -> Observed:
    """`op` under `intercept(interceptor)` with no observer at all."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().intercept(interceptor):
        result, error = await outcome_of(run())
    return Observed(Recorder(), result, error)


async def statement_after_the_query(interceptor: Any, *, pinned: bool) -> BaseException | None:
    """A `fetch_all` under `intercept(interceptor)` (it stores `execute`), then the stored
    `execute` is called again once the query is over: what that second call raised."""
    async with executor_of(pinned) as executor:
        with hooks().intercept(interceptor):
            await executor.fetch_all("SELECT 1", None)
        return await raised_by(interceptor.call_again())


async def task_left_behind(interceptor: Any, *, pinned: bool) -> tuple[Any, BaseException | None]:
    """A `fetch_all` whose interceptor starts `execute` in a task and returns without awaiting it;
    the task is awaited after the query (and its pin) is over: `(result, error of the task)`."""
    async with executor_of(pinned) as executor:
        with hooks().intercept(interceptor):
            result = await executor.fetch_all("SELECT 1", None)
    return result, await raised_by(interceptor.task)


# --------------------------------------------------------------------------- scope and order
async def child_of_intercepting_parent() -> PassThrough:
    """A `gather` child runs a statement while the parent's `intercept()` block is active."""
    interceptor = PassThrough()
    with hooks().intercept(interceptor):
        await in_gather_child(unobserved_fetch("SELECT 'child'"))
    return interceptor


async def interceptor_of_a_sibling() -> PassThrough:
    """Child A intercepts while sibling B runs a statement inside A's block; then A runs its own."""
    interceptor = PassThrough()
    a_intercepting, b_done = asyncio.Event(), asyncio.Event()

    async def a() -> None:
        with hooks().intercept(interceptor):
            a_intercepting.set()
            await b_done.wait()
            await unobserved_fetch("SELECT 'a'")

    async def b() -> None:
        await a_intercepting.wait()
        await unobserved_fetch("SELECT 'b'")
        b_done.set()

    await asyncio.gather(a(), b())
    return interceptor


async def intercepted_before_and_after_the_block() -> PassThrough:
    interceptor = PassThrough()
    with hooks().intercept(interceptor):
        await unobserved_fetch("SELECT 'inside'")
    await unobserved_fetch("SELECT 'after'")
    return interceptor


async def nested_intercept_blocks() -> list[str]:
    """`intercept(outer)` around `intercept(inner)`: one statement in both, one after the inner
    block exited."""
    log: list[str] = []
    with hooks().intercept(TaggedInterceptor(log, "outer")):
        with hooks().intercept(TaggedInterceptor(log, "inner")):
            await unobserved_fetch("SELECT 'both'")
        log.append("inner-exited")
        await unobserved_fetch("SELECT 'outer'")
    return log


async def two_scoped_interceptors() -> list[str]:
    log: list[str] = []
    with (
        hooks().intercept(TaggedInterceptor(log, "first")),
        hooks().intercept(TaggedInterceptor(log, "second")),
    ):
        await unobserved_fetch("SELECT 1")
    return log


async def global_and_scoped_interceptors() -> list[str]:
    """A scoped interceptor entered first, a global one registered after it."""
    log: list[str] = []
    with (
        hooks().intercept(TaggedInterceptor(log, "scoped")),
        interceptor_registered(TaggedInterceptor(log, "global")),
    ):
        await unobserved_fetch("SELECT 1")
    return log


async def two_global_interceptors() -> list[str]:
    log: list[str] = []
    with (
        interceptor_registered(TaggedInterceptor(log, "first")),
        interceptor_registered(TaggedInterceptor(log, "second")),
    ):
        await unobserved_fetch("SELECT 1")
    return log


async def observer_around_interceptor() -> list[str]:
    """An interceptor registered before an observer: the observer still wraps the chain."""
    log: list[str] = []
    with (
        hooks().intercept(TaggedInterceptor(log, "interceptor")),
        hooks().observe(Tagged(log, "observer")),
    ):
        await unobserved_fetch("SELECT 1")
    return log


async def global_interceptor_removed() -> PassThrough:
    interceptor = PassThrough()
    with interceptor_registered(interceptor):
        await unobserved_fetch("SELECT 'before'")
    await unobserved_fetch("SELECT 'after'")
    return interceptor


# --------------------------------------------------------------------------- transactions
@dataclass(frozen=True)
class InTransaction:
    result: Any
    error: BaseException | None
    broken: bool
    rollback_error: BaseException | None
    kinds: list[str]
    """The driver log after the rollback."""
    next_error: BaseException | None = None
    """What a plain `fetch_all` right after the intercepted call raised (when asked for)."""


async def intercepted_in_transaction(
    env: ExecEnv,
    interceptor: Any,
    *,
    queued: Any = None,
    op: str = "fetch_all",
    follow_up: bool = False,
) -> InTransaction:
    """On a pin: `begin()`, then `op` under `intercept(interceptor)` only (no observer), then (with
    `follow_up`) a plain `fetch_all`, then `rollback()`. `queued` (a result or an exception) is what
    the driver returns for `op`; a `LostConnection` counts as a lost connection."""
    env.default.disconnect_errors = (LostConnection,)
    if queued is not None:
        env.default.queue(queued)
    async with executor_module().pin(ALIAS) as executor:
        await executor.begin()
        with hooks().intercept(interceptor):
            result, error = await outcome_of(call(executor, op))
        broken = executor.broken
        next_error = await raised_by(executor.fetch_all("SELECT 2", None)) if follow_up else None
        rollback_error = await raised_by(executor.rollback())
        kinds = kinds_of(env.default)
    return InTransaction(result, error, broken, rollback_error, kinds, next_error)


async def cancel_waiting_interceptor_in_transaction(env: ExecEnv, *, after: bool) -> InTransaction:
    """On a pin after `begin()`, a `fetch_all` whose interceptor waits (before calling `execute`,
    or after it returned) and the owner task is really cancelled there. The worker catches what
    propagates, then rolls back."""
    interceptor = Waiting(after=after)
    seen = Seen()

    async def worker() -> None:
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            with hooks().intercept(interceptor):
                seen["error"] = await raised_by(executor.fetch_all("SELECT 1", None))
            seen["broken"] = executor.broken
            seen["rollback_error"] = await raised_by(executor.rollback())
            seen["kinds"] = kinds_of(env.default)

    task = seen.task = asyncio.create_task(worker())
    await interceptor.entered.wait()
    task.cancel()
    await asyncio.wait([task])
    return InTransaction(None, seen["error"], seen["broken"], seen["rollback_error"], seen["kinds"])


async def cancel_held_call_swallowed_by_interceptor(
    env: ExecEnv, interceptor: Any
) -> InTransaction:
    """In a transaction, a `fetch_all` held in the driver whose task is really cancelled there;
    `interceptor` swallows the cancellation and returns a result of its own. The worker then runs
    one more statement (`next_error`)."""
    seen = Seen()
    installed = asyncio.Event()

    async def worker() -> None:
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            hold = env.default.hold_statements()
            seen["hold"] = hold
            installed.set()
            with hooks().intercept(interceptor):
                seen["result"], seen["error"] = await outcome_of(
                    executor.fetch_all("SELECT 1", None)
                )
            seen["broken"] = executor.broken
            seen["next_error"] = await raised_by(executor.fetch_all("SELECT 2", None))

    task = seen.task = asyncio.create_task(worker())
    await installed.wait()
    await seen["hold"].wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return InTransaction(
        seen["result"],
        seen["error"],
        seen["broken"],
        None,
        kinds_of(env.default),
        seen["next_error"],
    )


HELD_IN_TRANSACTION = ("fetch_all", "stream-open", "stream-read", "stream-close", "savepoint")
"""Where the driver holds the call that gets cancelled: a statement, a stream's open, its first
chunk read, its close, or a transaction-control call."""


def _hold(env: ExecEnv, where: str) -> Any:
    driver = env.default
    if where in ("fetch_all", "stream-open"):
        return driver.hold_statements()
    if where == "stream-read":
        return driver.hold_chunks()
    if where == "stream-close":
        return driver.hold_stream_closes()
    if where == "savepoint":
        return driver.hold_controls()
    raise ValueError(f"unknown hold point {where!r}; expected one of {HELD_IN_TRANSACTION}")


async def _held_call(executor: Any, where: str) -> Any:
    if where not in HELD_IN_TRANSACTION:
        raise ValueError(f"unknown hold point {where!r}; expected one of {HELD_IN_TRANSACTION}")
    if where == "fetch_all":
        return await executor.fetch_all("SELECT 1", None)
    if where == "savepoint":
        return await executor.savepoint("sp")
    async with executor.stream(SQL, None, CHUNK) as chunks:
        return await anext(chunks)


async def cancel_held_call_under_interceptor(env: ExecEnv, where: str) -> tuple[Any, bool]:
    """In a transaction, with only an interceptor registered (no observer), a call is held in the
    driver at `where` and the owner task is really cancelled there: `(error, pin broken)`."""
    env.default.queue(list(ROWS))
    seen = Seen()
    installed = asyncio.Event()

    async def worker() -> None:
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            seen["hold"] = _hold(env, where)  # after BEGIN: a control hold would stop BEGIN too
            installed.set()
            with hooks().intercept(PassThrough()):
                seen["error"] = await raised_by(_held_call(executor, where))
            seen["broken"] = executor.broken

    task = seen.task = asyncio.create_task(worker())
    await installed.wait()
    await seen["hold"].wait_entered()
    task.cancel()
    await asyncio.wait([task])
    return seen["error"], seen["broken"]


async def intercepted_stream_and_controls(env: ExecEnv, interceptor: Any) -> tuple[Any, list[str]]:
    """With only `interceptor` registered: a pinned transaction that reads a stream in full, sets
    and releases a savepoint and commits: `(rows read, driver log)`."""
    from tests.support.aio import collect

    env.default.queue(list(ROWS))
    with hooks().intercept(interceptor):
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            async with executor.stream(SQL, None, CHUNK) as chunks:
                rows = await collect(chunks)
            await executor.savepoint("sp")
            await executor.release_savepoint("sp")
            await executor.commit()
    return rows, kinds_of(env.default)


# --------------------------------------------------------------------------- timing
async def timed_intercepted(
    env: ExecEnv, clock: FakeClock, op: str, *, pinned: bool, suppress: bool = False
) -> Observed:
    """`op` whose borrow waits `QUEUE` seconds (borrowed only), whose interceptor spends
    `INTERCEPTOR` seconds and whose driver call takes `QUERY` seconds (unless the interceptor
    supplies the result)."""
    env.default.during = clock.advancing(QUERY)
    supplied = supplied_result(op) if suppress else None
    interceptor = Advancing(clock, INTERCEPTOR, supplied)
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(recorder), hooks().intercept(interceptor):
        if pinned:
            result = await run()
        else:
            result = await with_held_borrow(env, clock, run)
    return Observed(recorder, result)
