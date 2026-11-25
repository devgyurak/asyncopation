"""Executor scenarios about the pinned-executor *context*: blocks that end in another context or in
another order than they began. Every function only *observes* and returns an outcome; the tests
assert."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import in_gather_child
from tests.support.executors.scenarios import ALIAS, SELECT, _executors, _registry


async def _pinning_generator() -> AsyncGenerator[Any]:
    async with _executors().pin(ALIAS) as executor:
        yield executor


@dataclass(frozen=True)
class Finalized:
    pin: Any
    current: Any
    rows: list[Any]
    in_flight: int


async def finalize_a_pinning_generator_in_another_task() -> Finalized:
    """An async generator holds a pin; another task finalizes it (what the event loop does with an
    abandoned generator). The original task then asks for the current executor and queries."""
    generator = _pinning_generator()
    executor = await anext(generator)
    await in_gather_child(generator.aclose())
    current = _executors().current_executor(ALIAS)
    rows = await current.fetch_all(SELECT, None)
    return Finalized(executor, current, rows, _registry().in_flight(ALIAS))


@dataclass(frozen=True)
class OutOfOrder:
    first: Any
    second: Any
    current_first_after_first_exit: Any
    current_second_after_first_exit: Any
    second_closed_at_that_lookup: bool
    current_first_after_both_exits: Any
    current_second_after_both_exits: Any


async def exit_two_pins_out_of_order() -> OutOfOrder:
    """Pin `default`, then `other`; leave `default` first (not the inner block first)."""
    mod = _executors()
    first_cm, second_cm = mod.pin("default"), mod.pin("other")
    first = await first_cm.__aenter__()
    second = await second_cm.__aenter__()
    await first_cm.__aexit__(None, None, None)
    current_first = mod.current_executor("default")
    current_second = mod.current_executor("other")
    second_closed = current_second.closed
    await second_cm.__aexit__(None, None, None)
    return OutOfOrder(
        first,
        second,
        current_first,
        current_second,
        second_closed,
        mod.current_executor("default"),
        mod.current_executor("other"),
    )


async def current_inside_a_pin_of_another_alias_pinned_first() -> tuple[Any, Any]:
    """`other` is pinned first, then `default`: the pin of `default` and what `default` resolves
    to inside both blocks."""
    mod = _executors()
    async with mod.pin("other"), mod.pin(ALIAS) as pinned:
        return pinned, mod.current_executor(ALIAS)


@dataclass(frozen=True)
class AliasNesting:
    first: Any
    current: Any
    nested: Any
    borrows: int


async def pin_another_alias_inside_a_pin(env: ExecEnv) -> AliasNesting:
    """`other` is pinned inside a pin of `default`; `default` is then pinned once more."""
    mod = _executors()
    async with mod.pin("default") as first, mod.pin("other"):
        current = mod.current_executor("default")
        async with mod.pin("default") as nested:
            pass
    return AliasNesting(first, current, nested, env.default.borrows)


async def pin_in_a_task_created_inside_a_pin() -> list[Any]:
    """A task made inside a block (it copied the context, so it sees the block's executor) pins
    the same alias after the block exited, and runs a statement."""
    release = asyncio.Event()

    async def late() -> list[Any]:
        await release.wait()
        async with _executors().pin(ALIAS) as executor:
            return await executor.fetch_all(SELECT, None)

    async with _executors().pin(ALIAS):
        task = asyncio.create_task(late())
    release.set()
    return await task


@dataclass(frozen=True)
class ForeignExit:
    other_pin: Any
    current_after: Any


async def exit_a_pin_from_a_task_that_pins_the_alias_itself() -> ForeignExit:
    """Task X (created before the pin) holds its own pin of the alias and ends the main task's pin
    (what a finalizer does): X's own pin must stay current in X."""
    mod = _executors()
    entered = asyncio.Event()
    main_pin = mod.pin(ALIAS)

    async def foreign() -> ForeignExit:
        async with mod.pin(ALIAS) as own:
            await entered.wait()
            await main_pin.__aexit__(None, None, None)
            return ForeignExit(own, mod.current_executor(ALIAS))

    task = asyncio.create_task(foreign())
    await main_pin.__aenter__()
    entered.set()
    return await task


async def query_in_a_task_created_after_the_block() -> list[Any]:
    """The block ended before the task was created: the task copies a context without the pin."""
    async with _executors().pin(ALIAS):
        pass

    async def query() -> list[Any]:
        return await _executors().current_executor(ALIAS).fetch_all(SELECT, None)

    return await asyncio.create_task(query())
