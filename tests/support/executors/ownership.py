"""Executor scenarios about which check wins when several apply (owner, busy, broken, closed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv, LostConnection
from tests.support.executors.ops import call, in_gather_child, kinds_of, raised_by
from tests.support.executors.scenarios import ALIAS, SELECT, _executors, broken_pin


@dataclass(frozen=True)
class ChildRollback:
    error: BaseException | None
    owner_read_error: BaseException | None
    kinds: list[str]


async def child_rollback_with_an_open_stream(env: ExecEnv) -> ChildRollback:
    """A child task rolls back the transaction while the owner's stream is open; then the owner
    reads the next chunk."""
    env.default.queue([(1,), (2,), (3,)])
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        async with executor.stream(SELECT, None, 1) as chunks:
            await anext(chunks)
            error = await raised_by(in_gather_child(executor.rollback()))
            owner_read_error = await raised_by(anext(chunks))
            kinds = kinds_of(env.default)
        await executor.rollback()
    return ChildRollback(error, owner_read_error, kinds)


async def child_use_of_a_broken_pin(env: ExecEnv, op: str) -> BaseException | None:
    """A child task runs `op` on a pin whose transaction was broken by the owner's cancellation."""
    async with broken_pin(env) as executor:
        return await raised_by(in_gather_child(call(executor, op)))


async def child_use_during_an_owner_stream(env: ExecEnv, op: str) -> BaseException | None:
    """A child task runs `op` on a pin while the owner's stream is open (the pin is busy)."""
    env.default.queue([(1,), (2,)])
    async with _executors().pin(ALIAS) as executor, executor.stream(SELECT, None, 1) as chunks:
        await anext(chunks)
        return await raised_by(in_gather_child(call(executor, op)))


async def broken_after_a_stream_body_raises(env: ExecEnv, error_type: type[BaseException]) -> bool:
    """The body of a pinned stream in a transaction raises `error_type`; is the pin broken after
    the stream closed? (`LostConnection` is classified as a lost connection by the driver.)"""
    env.default.disconnect_errors = (LostConnection,)
    env.default.queue([(1,), (2,)])

    async def body(executor: Any) -> None:
        async with executor.stream(SELECT, None, 1):
            raise error_type

    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        await raised_by(body(executor))
        return executor.broken


async def child_use_of_a_broken_pin_logged(env: ExecEnv, op: str) -> list[str]:
    """What the driver logged after a child task ran `op` on a pin broken by the owner."""
    async with broken_pin(env) as executor:
        await raised_by(in_gather_child(call(executor, op)))
        return kinds_of(env.default)
