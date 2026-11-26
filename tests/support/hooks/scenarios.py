"""Executor scenarios under observers, on the gated recording drivers (`execenv`).

Every function only *observes* and returns an outcome; the tests assert. Global observers added
here are always removed again, even when the scenario fails.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, call_with, in_gather_child, yield_once
from tests.support.executors.scenarios import ALIAS, executor_module, executor_of
from tests.support.hooks.events import Recorder, Tagged, hooks

PARAMETERIZED = "SELECT %s"


@dataclass
class Observed:
    """What one observed run produced: the recorder, and the result or the error raised."""

    recorder: Recorder
    result: Any = None
    error: BaseException | None = None
    injected: BaseException | None = None
    """The error a scenario made the driver raise (to compare by identity)."""
    cancelled: bool = False

    @property
    def names(self) -> list[str]:
        return self.recorder.names

    @property
    def started(self) -> Any:
        return self.recorder.only_started

    @property
    def finished(self) -> Any:
        return self.recorder.only_finished


async def outcome_of(awaitable: Awaitable[Any]) -> tuple[Any, BaseException | None]:
    try:
        return await awaitable, None
    except BaseException as exc:
        return None, exc


@contextlib.contextmanager
def registered(fn: Callable[[Any], None], **kwargs: Any) -> Generator[None]:
    """`add_observer(fn)` for the block; removed afterwards whatever happens."""
    module = hooks()
    module.add_observer(fn, **kwargs)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            module.remove_observer(fn)


async def observed(
    run: Callable[[], Awaitable[Any]], *, params: bool = False, recorder: Recorder | None = None
) -> Observed:
    """Run `run()` inside `observe(recorder, params=params)`."""
    recorder = recorder or Recorder()
    with hooks().observe(recorder, params=params):
        result, error = await outcome_of(run())
    return Observed(recorder, result, error)


async def observed_call(op: str, *, pinned: bool, params: bool = False) -> Observed:
    """One operation by name (`ops.call`) on a pinned or borrowed executor, observed."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    return await observed(run, params=params)


async def observed_call_with(
    op: str, sql: str, value: Any, *, pinned: bool, params: bool = False
) -> Observed:
    """One statement with explicit SQL and parameters (`ops.call_with`), observed."""

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call_with(executor, op, sql, value)

    return await observed(run, params=params)


async def observed_failure(env: ExecEnv, op: str, *, pinned: bool) -> Observed:
    """`op` while the driver raises a queued error for it."""
    error = ValueError("driver failure")
    env.default.queue(error)
    outcome = await observed_call(op, pinned=pinned)
    outcome.injected = error
    return outcome


async def unobserved_fetch(sql: str) -> Any:
    return await executor_module().BorrowedExecutor(ALIAS).fetch_all(sql, None)


# --------------------------------------------------------------------------- when events fire
@dataclass(frozen=True)
class Moments:
    while_waiting: list[str]
    after: list[str]


async def names_while_borrow_is_held(env: ExecEnv) -> Moments:
    """Event names seen while `driver.connection()` waits in the pool, and after the statement."""
    hold = env.default.hold_borrows()
    recorder = Recorder()
    with hooks().observe(recorder):
        task = asyncio.create_task(unobserved_fetch("SELECT 1"))
        await hold.wait_entered()
        waiting = list(recorder.names)
        hold.release()
        await task
    return Moments(waiting, recorder.names)


async def names_while_statement_is_held(env: ExecEnv, *, pinned: bool) -> Moments:
    """Event names seen while the driver call itself is in progress, and after it."""
    hold = env.default.hold_statements()
    recorder = Recorder()

    async def run() -> None:
        async with executor_of(pinned) as executor:
            await executor.fetch_all("SELECT 1", None)

    with hooks().observe(recorder):
        task = asyncio.create_task(run())
        await hold.wait_entered()
        waiting = list(recorder.names)
        hold.release()
        await task
    return Moments(waiting, recorder.names)


async def cancel_held_statement(env: ExecEnv, op: str, *, pinned: bool) -> Observed:
    """The task running `op` is cancelled while the driver holds the statement."""
    hold = env.default.hold_statements()
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    with hooks().observe(recorder):
        task = asyncio.create_task(run())
        await hold.wait_entered()
        task.cancel()
        await asyncio.wait([task])
    return Observed(recorder, cancelled=task.cancelled())


# --------------------------------------------------------------------------- params gating
@dataclass(frozen=True)
class TwoObservers:
    opted_in: Recorder
    opted_out: Recorder


async def opted_in_and_out() -> TwoObservers:
    """Two scoped observers on one statement, only the first asks for parameters."""
    opted_in, opted_out = Recorder(), Recorder()
    with hooks().observe(opted_in, params=True), hooks().observe(opted_out):
        await executor_module().BorrowedExecutor(ALIAS).fetch_all(PARAMETERIZED, (1,))
    return TwoObservers(opted_in, opted_out)


async def global_with_params() -> Recorder:
    recorder = Recorder()
    with registered(recorder, params=True):
        await executor_module().BorrowedExecutor(ALIAS).fetch_all(PARAMETERIZED, (1,))
    return recorder


# --------------------------------------------------------------------------- observer failures
@dataclass(frozen=True)
class WithFailing:
    result: Any
    error: BaseException | None
    other: Recorder
    failing_calls: int


async def with_failing_observer(
    failing: Callable[[Any], None], op: str, *, scope: str, pinned: bool
) -> WithFailing:
    """`op` observed by `failing` (scoped or global) and, after it, by a recording observer."""
    other = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    if scope == "global":
        with registered(failing), hooks().observe(other):
            result, error = await outcome_of(run())
    else:
        with hooks().observe(failing), hooks().observe(other):
            result, error = await outcome_of(run())
    return WithFailing(result, error, other, getattr(failing, "calls", -1))


# --------------------------------------------------------------------------- scope isolation
async def child_of_observed_parent() -> Recorder:
    """A `gather` child runs a statement while the parent's `observe()` block is active."""
    recorder = Recorder()
    with hooks().observe(recorder):
        await in_gather_child(unobserved_fetch("SELECT 'child'"))
    return recorder


async def observer_of_a_child() -> Recorder:
    """A `gather` child observes its own statement; the parent runs one after the child ended."""
    recorder = Recorder()

    async def child() -> None:
        with hooks().observe(recorder):
            await unobserved_fetch("SELECT 'child'")

    await in_gather_child(child())
    await unobserved_fetch("SELECT 'parent'")
    return recorder


async def observer_of_a_sibling() -> Recorder:
    """Child A observes while sibling B runs a statement inside A's block; then A runs its own."""
    recorder = Recorder()
    a_observing, b_done = asyncio.Event(), asyncio.Event()

    async def a() -> None:
        with hooks().observe(recorder):
            a_observing.set()
            await b_done.wait()
            await unobserved_fetch("SELECT 'a'")

    async def b() -> None:
        await a_observing.wait()
        await unobserved_fetch("SELECT 'b'")
        b_done.set()

    await asyncio.gather(a(), b())
    return recorder


async def statement_after_the_block() -> Recorder:
    recorder = Recorder()
    with hooks().observe(recorder):
        await unobserved_fetch("SELECT 'inside'")
    await unobserved_fetch("SELECT 'after'")
    return recorder


@dataclass(frozen=True)
class Nested:
    outer: Recorder
    inner: Recorder


async def nested_blocks() -> Nested:
    outer, inner = Recorder(), Recorder()
    with hooks().observe(outer):
        with hooks().observe(inner):
            await unobserved_fetch("SELECT 'inner'")
        await unobserved_fetch("SELECT 'outer'")
    return Nested(outer, inner)


async def child_starting_after_the_block() -> Recorder:
    """A task created inside `observe()` (it copied the context) runs its statement only after the
    block exited: the block is over for its children too."""
    recorder = Recorder()
    go = asyncio.Event()

    async def later() -> Any:
        await go.wait()
        return await unobserved_fetch("SELECT 'late child'")

    with hooks().observe(recorder):
        task = asyncio.create_task(later())
    go.set()
    await task
    return recorder


async def scoped_order() -> list[str]:
    log: list[str] = []
    with hooks().observe(Tagged(log, "first")), hooks().observe(Tagged(log, "second")):
        await unobserved_fetch("SELECT 1")
    return log


async def global_and_scoped_order() -> list[str]:
    log: list[str] = []
    with hooks().observe(Tagged(log, "scoped")), registered(Tagged(log, "global")):
        await unobserved_fetch("SELECT 1")
    return log


async def statement_with_unentered_observe() -> Recorder:
    """`observe(fn)` created but never entered: nothing is registered."""
    recorder = Recorder()
    hooks().observe(recorder)
    await unobserved_fetch("SELECT 1")
    return recorder


# --------------------------------------------------------------------------- global observers
async def global_seen_by_older_task() -> Recorder:
    """A task created before `add_observer` runs a statement after it: observed anyway."""
    recorder = Recorder()
    go = asyncio.Event()

    async def later() -> Any:
        await go.wait()
        return await unobserved_fetch("SELECT 'task'")

    task = asyncio.create_task(later())
    await yield_once()
    with registered(recorder):
        go.set()
        await task
    return recorder


async def global_removed() -> Recorder:
    recorder = Recorder()
    with registered(recorder):
        await unobserved_fetch("SELECT 'before'")
    await unobserved_fetch("SELECT 'after'")
    return recorder


# --------------------------------------------------------------------------- zero subscribers
class SentinelConnection:
    """A connection whose every method returns a fresh object and remembers it."""

    def __init__(self) -> None:
        self.produced: list[object] = []

    def __getattr__(self, name: str) -> Callable[..., object]:
        def method(*args: Any) -> object:
            out = object()
            self.produced.append(out)
            return out

        return method


ARGS: dict[str, tuple[Any, ...]] = {
    "fetch_all": ("SELECT 1", None),
    "execute": ("UPDATE t SET x = 1", None),
    "execute_many": ("INSERT INTO t VALUES (%s)", [(1,), (2,)]),
    "stream": ("SELECT 1", None, 10),
    "begin": (),
    "commit": (),
    "rollback": (),
    "savepoint": ("sp",),
    "release_savepoint": ("sp",),
    "rollback_to_savepoint": ("sp",),
}


@dataclass(frozen=True)
class Passthrough:
    returned: object
    produced: list[object] = field(default_factory=list)

    @property
    def same_object(self) -> bool:
        return len(self.produced) == 1 and self.returned is self.produced[0]


def run_without_subscribers(kind: str) -> Passthrough:
    """`_run` with no subscriber: what it returned and what the connection method produced."""
    connection = SentinelConnection()
    returned = executor_module()._run(connection, (), ALIAS, None, kind, *ARGS[kind])
    return Passthrough(returned, connection.produced)
