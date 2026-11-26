"""How long a registration lasts and what misuse of an observer does (ADR-0005, review round 1).

Every function only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
import gc
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, in_gather_child, raised_by
from tests.support.executors.scenarios import executor_module, executor_of
from tests.support.hooks.events import Recorder, WithMethod, hooks
from tests.support.hooks.scenarios import Observed, outcome_of, registered, unobserved_fetch
from tests.support.hooks.streams import CHUNK, ROWS, SQL


# --------------------------------------------------------------------------- exits out of order
@dataclass(frozen=True)
class OutOfOrder:
    a: Recorder
    b: Recorder


async def exit_out_of_order() -> OutOfOrder:
    """Enter `observe(a)` then `observe(b)`, exit `a` first, then `b`; a statement runs after each
    exit ("SELECT 'a-exited'", "SELECT 'both-exited'")."""
    a, b = Recorder(), Recorder()
    block_a, block_b = hooks().observe(a), hooks().observe(b)
    block_a.__enter__()
    block_b.__enter__()
    block_a.__exit__(None, None, None)
    await unobserved_fetch("SELECT 'a-exited'")
    block_b.__exit__(None, None, None)
    await unobserved_fetch("SELECT 'both-exited'")
    return OutOfOrder(a, b)


# --------------------------------------------------------------------------- exits in another Context
@dataclass(frozen=True)
class ForeignExit:
    error: BaseException | None
    exited: Recorder
    other: Recorder
    still_held: bool
    """Whether the entering Context's scoped tuple still holds the exited registration after a
    statement ran there."""


def scoped_holds(fn: Any) -> bool:
    """Whether the current Context's scoped tuple holds a registration of `fn`."""
    return any(subscription.fn is fn for subscription in hooks()._scoped.get())


async def _observing_generator(recorder: Recorder) -> AsyncGenerator[int]:
    """Holds `observe(recorder)` across its yields."""
    with hooks().observe(recorder):
        yield 1
        yield 2


async def _first_by_break(generator: AsyncGenerator[int]) -> None:
    async for _item in generator:
        break


async def generator_block_closed_by_another_task() -> ForeignExit:
    """An async generator enters `observe(f)` in this task (`async for ...: break` leaves it
    suspended inside the block), and another task closes it with `aclose()`; statements run before
    and after the close in the entering task, under another, still open registration."""
    exited, other = Recorder(), Recorder()
    with hooks().observe(other):
        generator = _observing_generator(exited)
        await _first_by_break(generator)
        await unobserved_fetch("SELECT 'open'")
        error = await raised_by(in_gather_child(generator.aclose()))
        await unobserved_fetch("SELECT 'closed'")
        held = scoped_holds(exited)
    return ForeignExit(error, exited, other, held)


async def block_exited_in_a_gather_child() -> ForeignExit:
    """`observe(f).__enter__()` in this task, `__exit__` inside a `gather` child; statements run
    before and after the exit in the entering task, under another, still open registration."""
    exited, other = Recorder(), Recorder()
    block = hooks().observe(exited)

    async def leave() -> None:
        block.__exit__(None, None, None)

    with hooks().observe(other):
        block.__enter__()
        await unobserved_fetch("SELECT 'open'")
        error = await raised_by(in_gather_child(leave()))
        await unobserved_fetch("SELECT 'closed'")
        held = scoped_holds(exited)
    return ForeignExit(error, exited, other, held)


async def child_block_open_while_parent_runs() -> tuple[Recorder, Recorder]:
    """A child holds `observe(child)` open while the parent (observed by `parent`) runs a statement
    concurrently; then the child runs its own. Returns `(child, parent)`."""
    child, parent = Recorder(), Recorder()
    child_observing, parent_done = asyncio.Event(), asyncio.Event()

    async def child_task() -> None:
        with hooks().observe(child):
            child_observing.set()
            await parent_done.wait()
            await unobserved_fetch("SELECT 'child'")

    with hooks().observe(parent):
        task = asyncio.create_task(child_task())
        await child_observing.wait()
        await unobserved_fetch("SELECT 'parent'")
        parent_done.set()
        await task
    return child, parent


# --------------------------------------------------------------------------- global registrations
async def same_observer_added_twice() -> list[int]:
    """`add_observer(rec)` twice: events after one statement, after one `remove_observer`, and after
    the second one."""
    recorder = Recorder()
    module = hooks()
    module.add_observer(recorder)
    module.add_observer(recorder)
    counts: list[int] = []
    try:
        await unobserved_fetch("SELECT 1")
        counts.append(len(recorder.events))
        module.remove_observer(recorder)
        await unobserved_fetch("SELECT 1")
        counts.append(len(recorder.events))
        module.remove_observer(recorder)
        await unobserved_fetch("SELECT 1")
        counts.append(len(recorder.events))
    finally:
        _remove_all(recorder)
    return counts


async def remove_keeps_the_older_registration() -> list[Any]:
    """`add_observer(rec, params=True)` then `add_observer(rec)`; one `remove_observer(rec)` must
    remove the newer one, so the remaining registration still delivers the parameters."""
    recorder = Recorder()
    module = hooks()
    module.add_observer(recorder, params=True)
    module.add_observer(recorder)
    try:
        module.remove_observer(recorder)
        await executor_module().BorrowedExecutor("default").fetch_all("SELECT %s", (1,))
    finally:
        _remove_all(recorder)
    return recorder.params_seen


def _remove_all(fn: Any) -> None:
    module = hooks()
    module._global = tuple(s for s in module._global if s.fn is not fn)


async def global_removed_while_stream_open(env: ExecEnv, *, pinned: bool) -> Recorder:
    """A global observer is removed while a stream it saw open is still open."""
    env.default.queue(list(ROWS))
    recorder = Recorder()
    async with executor_of(pinned) as executor:
        with registered(recorder):
            manager = executor.stream(SQL, None, CHUNK)
            chunks = await manager.__aenter__()
            await anext(chunks)
        await manager.__aexit__(None, None, None)
    return recorder


# --------------------------------------------------------------------------- misbehaving observers
async def observer_raising(
    observer: Any, op: str, *, pinned: bool, scope: str = "scoped"
) -> Observed:
    """`op` observed by `observer` (scoped or global) and by a recorder."""
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(pinned) as executor:
            return await call(executor, op)

    if scope == "global":
        with registered(observer), hooks().observe(recorder):
            result, error = await outcome_of(run())
    else:
        with hooks().observe(observer), hooks().observe(recorder):
            result, error = await outcome_of(run())
    return Observed(recorder, result, error)


@dataclass(frozen=True)
class MisusedObserver:
    result: Any
    error: BaseException | None
    names: list[str]
    never_awaited: list[str]
    """RuntimeWarnings about `async_observer` never being awaited (garbage collection included)."""


async def observer_returning_an_awaitable(observer: Any) -> MisusedObserver:
    """`observer` (it returns an awaitable from each call) observes one `fetch_all`."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with hooks().observe(observer):
            result, error = await outcome_of(unobserved_fetch("SELECT 1"))
        gc.collect()
    found = [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "async_observer" in str(w.message)
    ]
    return MisusedObserver(result, error, observer.names, found)


@dataclass(frozen=True)
class Aborted:
    observed: Observed
    statements: list[Any]
    gate_calls: int


async def aborted_by_started_observer(
    env: ExecEnv, observer: Any, op: str, *, pinned: bool
) -> Aborted:
    """`op` whose `query_started` observer raises a BaseException: what the driver saw."""
    observed = await observer_raising(observer, op, pinned=pinned)
    return Aborted(observed, env.default.statements, env.default.statement_calls)


async def bound_method_registration() -> tuple[list[str], list[str]]:
    """`add_observer(obj.on_query)` then `remove_observer(obj.on_query)` (another bound-method
    object): SQL seen while registered, and after the removal."""
    holder = WithMethod()
    module = hooks()
    module.add_observer(holder.on_query)
    try:
        await unobserved_fetch("SELECT 'registered'")
        before = list(holder.recorder.sqls)
        module.remove_observer(holder.on_query)
        await unobserved_fetch("SELECT 'removed'")
    finally:
        module._global = tuple(s for s in module._global if s.fn != holder.on_query)
    return before, holder.recorder.sqls


async def cancel_held_control(env: ExecEnv, op: str) -> Observed:
    """The task running transaction control `op` on a pin is cancelled inside the driver call."""
    hold = env.default.hold_controls()
    recorder = Recorder()

    async def run() -> Any:
        async with executor_of(True) as executor:
            return await call(executor, op)

    with hooks().observe(recorder):
        task = asyncio.create_task(run())
        await hold.wait_entered()
        task.cancel()
        await asyncio.wait([task])
    return Observed(recorder, cancelled=task.cancelled())


async def reversed_release(env: ExecEnv) -> Recorder:
    """Two concurrent borrowed fetches held in the driver, released in reverse order; each SQL
    has its own result (one row and two rows)."""
    first, second = "SELECT 'first'", "SELECT 'second'"
    env.default.results_by_sql.update({first: [(1,)], second: [(1,), (2,)]})
    first_hold, second_hold = env.default.hold_sql(first), env.default.hold_sql(second)
    recorder = Recorder()
    with hooks().observe(recorder):
        tasks = [asyncio.create_task(unobserved_fetch(first))]
        await first_hold.wait_entered()
        tasks.append(asyncio.create_task(unobserved_fetch(second)))
        await second_hold.wait_entered()
        second_hold.release()
        await tasks[1]
        first_hold.release()
        await tasks[0]
    return recorder


# --------------------------------------------------------------------------- alias and passthrough
@asynccontextmanager
async def _executor_on(alias: str, pinned: bool) -> AsyncGenerator[Any]:
    module = executor_module()
    if pinned:
        async with module.pin(alias) as executor:
            yield executor
    else:
        yield module.BorrowedExecutor(alias)


async def observed_on_alias(alias: str, op: str, *, pinned: bool) -> Recorder:
    recorder = Recorder()
    with hooks().observe(recorder):
        async with _executor_on(alias, pinned) as executor:
            await call(executor, op)
    return recorder


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("observed_call was used although nobody observes")


async def run_with_observed_call_refused(monkeypatch: Any, op: str, *, pinned: bool) -> Any:
    """`op` with no observer while `hooks.observed_call` raises: the plain path never uses it."""
    monkeypatch.setattr(hooks(), "observed_call", _refuse)
    async with executor_of(pinned) as executor:
        return await raised_by(call(executor, op))


async def failed_control(env: ExecEnv) -> Observed:
    """`begin()` on a pinned executor while the driver's BEGIN raises."""
    error = ValueError("begin failed")
    env.default.fail_next("begin", error)
    recorder = Recorder()
    with hooks().observe(recorder):
        async with executor_of(True) as executor:
            result, raised = await outcome_of(executor.begin())
    return Observed(recorder, result, raised, error)
