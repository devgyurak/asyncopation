"""Executor scenarios about using a pin whose transaction is broken. Every function only
*observes* and returns an outcome; the tests assert."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, kinds_of, raised_by
from tests.support.executors.scenarios import broken_pin, lost_connection_pin

CAUSES = ["cancelled_statement", "lost_connection"]


def _broken(env: ExecEnv, cause: str) -> AbstractAsyncContextManager[Any]:
    """A pin broken by `cause`: a cancelled statement or a statement that lost the connection."""
    return {"cancelled_statement": broken_pin, "lost_connection": lost_connection_pin}[cause](env)


@dataclass(frozen=True)
class BrokenUse:
    error: BaseException | None
    calls_before: int
    calls_after: int


async def use_a_broken_pin(
    env: ExecEnv, op: str, *, cause: str = "cancelled_statement"
) -> BrokenUse:
    """`op` on a pin broken by `cause`: the error and the driver's statement calls around it."""
    async with _broken(env, cause) as executor:
        before = env.default.statement_calls
        error = await raised_by(call(executor, op))
        return BrokenUse(error, before, env.default.statement_calls)


@dataclass(frozen=True)
class BrokenRollback:
    errors: list[BaseException | None]
    kinds: list[str]


async def roll_back_a_broken_pin(
    env: ExecEnv, *, times: int = 1, cause: str = "cancelled_statement"
) -> BrokenRollback:
    """`rollback()` `times` times on a pin broken by `cause`: what each call raised (`None`: it returned)."""
    async with _broken(env, cause) as executor:
        errors = [await raised_by(executor.rollback()) for _ in range(times)]
        return BrokenRollback(errors, kinds_of(env.default))


async def other_alias_beside_a_broken_pin(env: ExecEnv) -> list[Any]:
    """A statement on the pin of `other`, inside the block of a broken pin of `default`."""
    from tests.support.executors.scenarios import _executors

    async with broken_pin(env), _executors().pin("other") as other:
        return await other.fetch_all("SELECT 1", None)
