"""Interceptors for the ADR-0005 interceptor tests (issue #4): each one is an async callable
`fn(execute, sql, params, many, context)` that records what it was given, so the tests can assert.

Interceptors that misbehave on purpose (wrong `many`, a changed method, a wrong result type, a
synchronous function, `execute` in another task or after the query) live here too.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager, suppress
from typing import Any, NamedTuple

from asyncopation.drivers.base import ExecResult
from tests.support.hooks.events import hooks

INTERCEPTABLE_OPS = ["fetch_all", "execute", "execute_many"]
NOT_INTERCEPTABLE_OPS = ["stream"]


class Given(NamedTuple):
    """What one interceptor call was given."""

    sql: str
    params: Any
    many: bool
    context: dict[str, Any]


class PassThrough:
    """Awaits `execute` with exactly what it was given and returns its result."""

    def __init__(self) -> None:
        self.calls: list[Given] = []

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.calls.append(Given(sql, params, many, dict(context)))
        return await execute(sql, params, many, context)

    @property
    def sqls(self) -> list[str]:
        return [given.sql for given in self.calls]


class TaggedInterceptor:
    """Appends `<tag>:before` / `<tag>:after` around `execute` to a shared log."""

    def __init__(self, log: list[str], tag: str) -> None:
        self.log = log
        self.tag = tag

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.log.append(f"{self.tag}:before")
        result = await execute(sql, params, many, context)
        self.log.append(f"{self.tag}:after")
        return result


class Suppressing:
    """Returns `result` without calling `execute` (the query never reaches the driver)."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.calls += 1
        return self.result


class Rewriting:
    """Passes `sql` / `params` of its own to `execute`."""

    def __init__(self, sql: str, params: Any) -> None:
        self.sql = sql
        self.params = params

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await execute(self.sql, self.params, many, context)


class Prefixing:
    """Prepends `prefix` to the SQL (an annotation such as a comment)."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await execute(self.prefix + sql, params, many, context)


class RaisingBefore:
    """Raises `error` (any BaseException) before calling `execute`."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        raise self.error


class RaisingAfter:
    """Awaits `execute`, then raises `error` (any BaseException)."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.returned: Any = None

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.returned = await execute(sql, params, many, context)
        raise self.error


class MethodChanging:
    """Calls `execute` with `context["method"]` replaced by `method`."""

    def __init__(self, method: str) -> None:
        self.method = method

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await execute(sql, params, many, {**context, "method": self.method})


class Switching:
    """Calls `execute` as another method: `context["method"]`, `many` and the parameters replaced."""

    def __init__(self, method: str, many: bool, params: Any) -> None:
        self.method = method
        self.many = many
        self.params = params

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await execute(sql, self.params, self.many, {**context, "method": self.method})


class ManyFlipping:
    """Calls `execute` with `many` negated (it no longer matches the method)."""

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await execute(sql, params, not many, context)


class Registrable:
    """A callable that type-checks as an observer and as an interceptor (registration tests that
    use one function as both); it is never called."""

    def __call__(self, *args: Any) -> Any:
        raise AssertionError("a registration-only callable was called")


def synchronous_interceptor(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
    """A plain function (wrongly) registered as an interceptor: returns rows, not an awaitable."""
    return [(1,)]


class Returning:
    """Returns `value` without calling `execute` (a wrong type for the method, on purpose)."""

    def __init__(self, value: Any) -> None:
        self.value = value

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return self.value


class GatheringTwice:
    """Runs two `execute` calls at once with `asyncio.gather` (child tasks)."""

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        first, _second = await asyncio.gather(
            execute(sql, params, many, context), execute(sql, params, many, context)
        )
        return first


class InAnotherTask:
    """Runs `execute` in a task of its own and awaits that task."""

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        return await asyncio.create_task(execute(sql, params, many, context))


class LeavingATask:
    """Starts `execute` in a task of its own, returns `supplied` without awaiting it; the task is
    kept in `task` (the test awaits it after the query is over)."""

    def __init__(self, supplied: Any) -> None:
        self.supplied = supplied
        self.task: asyncio.Task[Any] | None = None

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.task = asyncio.create_task(execute(sql, params, many, context))
        return self.supplied


class StoringExecute:
    """Passes through and keeps `execute` and what it was given, to call it again later."""

    def __init__(self) -> None:
        self.execute: Any = None
        self.given: Given | None = None

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.execute = execute
        self.given = Given(sql, params, many, context)
        return await execute(sql, params, many, context)

    async def call_again(self) -> Any:
        """Call the stored `execute` with what it was given the first time."""
        assert self.given is not None, "the interceptor never ran"
        return await self.execute(*self.given)


class Retrying:
    """Calls `execute`; when it raises a `catch`, calls it once more (a sequential retry)."""

    def __init__(self, catch: type[BaseException] = Exception) -> None:
        self.catch = catch
        self.first_error: BaseException | None = None

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        try:
            return await execute(sql, params, many, context)
        except self.catch as exc:
            self.first_error = exc
        return await execute(sql, params, many, context)


def failing_once(driver: Any, error: BaseException) -> Any:
    """A `GatedDriver.during` hook: the next statement raises `error` at the gate, before the
    recording connection runs it (so the fake does not discard the connection and a retry can
    succeed); later statements run normally."""

    async def fail() -> None:
        driver.during = None
        raise error

    return fail


class Catching:
    """Calls `execute`; whatever it raises (a cancellation included) is kept in `caught` and
    `supplied` is returned instead."""

    def __init__(self, supplied: Any) -> None:
        self.supplied = supplied
        self.caught: BaseException | None = None

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        try:
            return await execute(sql, params, many, context)
        except BaseException as exc:
            self.caught = exc
        return self.supplied


class Waiting:
    """Waits forever, before calling `execute` or after it returned; `entered` is set when it
    starts waiting (the test cancels the task there)."""

    def __init__(self, *, after: bool) -> None:
        self.after = after
        self.entered = asyncio.Event()

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if self.after:
            await execute(sql, params, many, context)
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the waiting interceptor was never cancelled")


class Advancing:
    """Spends `seconds` of a fake clock before calling `execute` (or before returning `supplied`
    instead of calling it)."""

    def __init__(self, clock: Any, seconds: float, supplied: Any = None) -> None:
        self.clock = clock
        self.seconds = seconds
        self.supplied = supplied

    async def __call__(self, execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        self.clock.advance(self.seconds)
        if self.supplied is not None:
            return self.supplied
        return await execute(sql, params, many, context)


# --------------------------------------------------------------------------- results
def driver_result(op: str) -> Any:
    """A fresh result for the driver to return for `op` (queue it, then compare by identity)."""
    if op == "fetch_all":
        return [(1,), (2,)]
    if op == "execute":
        return ExecResult(rowcount=3)
    if op == "execute_many":
        return ExecResult(rowcount=2)
    raise ValueError(f"not an interceptable statement: {op!r}")


def supplied_result(op: str) -> Any:
    """A fresh result an interceptor supplies instead of running `op`."""
    if op == "fetch_all":
        return [(7,), (8,), (9,)]
    if op == "execute":
        return ExecResult(rowcount=5)
    if op == "execute_many":
        return ExecResult(rowcount=4)
    raise ValueError(f"not an interceptable statement: {op!r}")


SUPPLIED_ROWCOUNT = {"fetch_all": 3, "execute": 5, "execute_many": 4}
"""The rowcount of `supplied_result(op)`: `len()` of the rows, else `ExecResult.rowcount`."""


# --------------------------------------------------------------------------- registration
@contextmanager
def interceptor_registered(fn: Any) -> Generator[None]:
    """`add_interceptor(fn)` for the block; removed afterwards whatever happens."""
    module = hooks()
    module.add_interceptor(fn)
    try:
        yield
    finally:
        with suppress(ValueError):
            module.remove_interceptor(fn)
