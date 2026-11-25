"""Executor scenarios about the choke point (`_run`) and the connection protocol."""

from __future__ import annotations

import inspect
from typing import Any

from tests.support.executors.fakes import ExecEnv
from tests.support.executors.ops import call, kinds_of
from tests.support.executors.scenarios import ALIAS, _executors, executor_of


def connection_methods() -> set[str]:
    """Names of every public member of the `AsyncDbConnection` protocol."""
    from asyncopation.drivers.base import AsyncDbConnection

    members = inspect.getmembers(AsyncDbConnection, callable)
    return {name for name, _member in members if not name.startswith("_")}


async def kinds_seen_by_run(monkeypatch: Any, op: str, *, pinned: bool) -> list[str]:
    """The kinds the choke point `_run` saw while one operation ran on an executor."""
    module = _executors()
    seen: list[str] = []
    real = module._run

    def spy(connection: Any, kind: str, *args: Any) -> Any:
        seen.append(kind)
        return real(connection, kind, *args)

    monkeypatch.setattr(module, "_run", spy)
    async with executor_of(pinned) as executor:
        await call(executor, op)
    return seen


async def logged_kinds_of_a_transaction(env: ExecEnv) -> list[str]:
    """BEGIN, a savepoint round and COMMIT through a pinned executor, as the driver logged them."""
    async with _executors().pin(ALIAS) as executor:
        await executor.begin()
        await executor.savepoint("sp")
        await executor.rollback_to_savepoint("sp")
        await executor.release_savepoint("sp")
        await executor.commit()
        return kinds_of(env.default)


def transaction_controls_of_borrowed_executor() -> set[str]:
    """The transaction-control calls a `BorrowedExecutor` exposes (it has none by design)."""
    from asyncopation.connection.executor import BorrowedExecutor
    from tests.support.executors.ops import CONTROLS

    return set(CONTROLS) & set(dir(BorrowedExecutor))
