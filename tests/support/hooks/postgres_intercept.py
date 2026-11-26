"""Interceptors on a real PostgreSQL connection (the `spy` fixture): rewritten and annotated SQL,
suppressed statements, and a pinned transaction whose interceptor is cancelled.

Every function only *observes* and returns an outcome; the tests assert.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from tests.support.executors.ops import raised_by
from tests.support.executors.postgres import in_open_transaction
from tests.support.executors.scenarios import executor_module
from tests.support.hooks.events import Recorder, hooks
from tests.support.hooks.interceptors import Prefixing, Rewriting, Suppressing, Waiting
from tests.support.hooks.scenarios import Observed, outcome_of
from tests.support.hooks.transactions import Seen

ALIAS = "default"
ANNOTATION = "/* asyncopation-interceptor */ "
CURRENT_QUERY = "SELECT current_query()"
MISSING_TABLE = "SELECT * FROM asyncopation_no_such_table"
SUPPLIED = [(42,), (43,)]


@asynccontextmanager
async def _executor(pinned: bool) -> Any:
    module = executor_module()
    if pinned:
        async with module.pin(ALIAS) as executor:
            yield executor
    else:
        yield module.BorrowedExecutor(ALIAS)


async def _intercepted(interceptor: Any, sql: str, params: Any, *, pinned: bool) -> Observed:
    recorder = Recorder()

    async def run() -> Any:
        async with _executor(pinned) as executor:
            return await executor.fetch_all(sql, params)

    with hooks().observe(recorder), hooks().intercept(interceptor):
        result, error = await outcome_of(run())
    return Observed(recorder, result, error)


async def annotated_real_fetch(*, pinned: bool) -> Observed:
    """`SELECT current_query()` under an interceptor that prepends a SQL comment: the server
    reports the text it really ran."""
    return await _intercepted(Prefixing(ANNOTATION), CURRENT_QUERY, None, pinned=pinned)


async def rewritten_real_fetch(*, pinned: bool) -> Observed:
    """`SELECT %s::int` with `(1,)`, rewritten to other SQL and parameters by an interceptor."""
    rewriting = Rewriting("SELECT %s::int * 10", (2,))
    return await _intercepted(rewriting, "SELECT %s::int", (1,), pinned=pinned)


async def suppressed_real_fetch(*, pinned: bool) -> Observed:
    """A query on a table that does not exist, suppressed by an interceptor (the server would
    raise if it ever received it)."""
    return await _intercepted(Suppressing(list(SUPPLIED)), MISSING_TABLE, None, pinned=pinned)


@dataclass(frozen=True)
class SuppressedInTransaction:
    result: Any
    error: BaseException | None
    still_in_transaction: bool
    """A failed statement would have aborted the PostgreSQL transaction."""


async def suppressed_in_real_transaction() -> SuppressedInTransaction:
    """In a real pinned transaction: a statement, a suppressed query on a missing table, then a
    check that the transaction is still usable; rolled back at the end."""
    async with executor_module().pin(ALIAS) as executor:
        await executor.begin()
        await executor.fetch_all("SELECT 1", None)
        with hooks().intercept(Suppressing(list(SUPPLIED))):
            result, error = await outcome_of(executor.fetch_all(MISSING_TABLE, None))
        usable = await in_open_transaction(executor)
        await executor.rollback()
    return SuppressedInTransaction(result, error, usable)


@dataclass(frozen=True)
class CancelledInTransaction:
    error: BaseException | None
    broken: bool
    still_in_transaction: bool
    rollback_error: BaseException | None
    table_after_rollback: Any
    """`to_regclass` of the temporary table created in the transaction, after the rollback
    (`None`: the rollback really undid it)."""


async def cancel_interceptor_in_real_transaction(*, after: bool) -> CancelledInTransaction:
    """In a real pinned transaction that created a temporary table, a `fetch_all` whose interceptor
    waits (before calling `execute`, or after it returned) is really cancelled; the worker then
    checks the transaction, rolls back and looks for the table."""
    table = f"asyncopation_icpt_{uuid.uuid4().hex[:12]}"
    interceptor = Waiting(after=after)
    seen = Seen()

    async def worker() -> None:
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            await executor.execute(f"CREATE TEMPORARY TABLE {table} (id int)", None)
            with hooks().intercept(interceptor):
                seen["error"] = await raised_by(executor.fetch_all("SELECT 1", None))
            seen["broken"] = executor.broken
            seen["usable"] = await in_open_transaction(executor)
            seen["rollback_error"] = await raised_by(executor.rollback())
            rows = await executor.fetch_all("SELECT to_regclass(%s)::text", (f"pg_temp.{table}",))
            seen["table"] = rows[0][0]

    task = seen.task = asyncio.create_task(worker())
    await interceptor.entered.wait()
    task.cancel()
    await asyncio.wait([task])
    return CancelledInTransaction(
        seen["error"], seen["broken"], seen["usable"], seen["rollback_error"], seen["table"]
    )
