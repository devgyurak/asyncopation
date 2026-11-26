"""Sync ORM vs `aq(...)` for the read APIs (`aget`, `afirst`, `acount`, `aexists`, `async for`).

Unlike `run_differential`, an error is compared by type **and message**: the messages of
`DoesNotExist` / `MultipleObjectsReturned` are part of Django's contract (control flow lives here).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import NotSupportedError, transaction

from asyncopation import aq
from asyncopation.connection import registry
from asyncopation.connection.executor import pin
from tests.support.aio import collect, wait_until
from tests.support.alist_cases import ALIAS, pool_in_use, wait_pool_settled
from tests.support.alist_probe import recorded_statements
from tests.support.connect_counter import count_sync_connection_attempts
from tests.support.seeded import in_sync
from tests.support.snapshot import Snapshot, snapshot_result

type Build = Callable[[], Any]

SLEEP_SECONDS = 3  # long enough that three statements started together overlap with a wide margin
SLEEP_SQL = f"(SELECT pg_sleep({SLEEP_SECONDS})) IS NOT NULL"
SYNC_NAMES = {
    "alist": "list",
    "aget": "get",
    "afirst": "first",
    "acount": "count",
    "aexists": "exists",
}


@dataclass(frozen=True)
class Reading:
    """A result (as a snapshot) or an error (type and message)."""

    value: Snapshot | None
    error: tuple[str, str] | None

    @property
    def error_type(self) -> str | None:
        return None if self.error is None else self.error[0]


@dataclass(frozen=True)
class Pair:
    sync: Reading
    async_: Reading

    @property
    def agree(self) -> bool:
        return self.sync == self.async_


def _error(exc: BaseException) -> tuple[str, str]:
    return f"{type(exc).__module__}.{type(exc).__qualname__}", str(exc)


def _sync_call(build: Build, op: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    queryset = build()
    if op == "alist":
        return list(queryset)
    return getattr(queryset, SYNC_NAMES[op])(*args, **kwargs)


async def sync_reading(function: Callable[[], Any]) -> Reading:
    def evaluate() -> Reading:
        try:
            return Reading(snapshot_result(function()), None)
        except Exception as exc:
            return Reading(None, _error(exc))

    return await in_sync(evaluate)


async def async_reading(function: Callable[[], Any]) -> Reading:
    try:
        return Reading(snapshot_result(await function()), None)
    except Exception as exc:
        return Reading(None, _error(exc))


async def compare(build: Build, op: str, *args: Any, **kwargs: Any) -> Pair:
    """`build().<sync op>(...)` against `await aq(build()).<op>(...)`, each on a fresh QuerySet."""
    sync = await sync_reading(lambda: _sync_call(build, op, args, kwargs))
    async_ = await async_reading(lambda: getattr(aq(build()), op)(*args, **kwargs))
    return Pair(sync, async_)


def _locked_sync(build: Build, op: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    with transaction.atomic():
        return _sync_call(build, op, args, kwargs)


async def _locked_async(
    build: Build, op: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    async with pin(ALIAS):
        return await getattr(aq(build()), op)(*args, **kwargs)


async def compare_in_transaction(build: Build, op: str, *args: Any, **kwargs: Any) -> Pair:
    """The sync side inside `transaction.atomic()`, the async side inside `pin()`."""
    sync = await sync_reading(lambda: _locked_sync(build, op, args, kwargs))
    async_ = await async_reading(lambda: _locked_async(build, op, args, kwargs))
    return Pair(sync, async_)


@dataclass(frozen=True)
class CachedRead:
    """`alist()` first, then `<op>` on the same wrapper: the answer and the statements it ran."""

    value: Snapshot
    statements: int


async def read_with_filled_cache(build: Build, op: str) -> CachedRead:
    wrapper = aq(build())
    await wrapper.alist()
    with recorded_statements() as log:
        result = await getattr(wrapper, op)()
    return CachedRead(snapshot_result(result), len(log))


async def sync_value(build: Build, op: str) -> Snapshot:
    reading = await sync_reading(lambda: _sync_call(build, op, (), {}))
    return reading.value or ()


@dataclass(frozen=True)
class Iterated:
    first: Snapshot
    second: Snapshot
    second_statements: int
    cached: bool


async def iterate_twice(build: Build) -> Iterated:
    """`async for` over a wrapper twice: rows, rows again, statements of the second pass, cache."""
    wrapper = aq(build())
    first = await collect(wrapper)
    with recorded_statements() as log:
        second = await collect(wrapper)
    return Iterated(
        snapshot_result(first),
        snapshot_result(second),
        len(log),
        wrapper.queryset._result_cache is not None,
    )


@dataclass(frozen=True)
class Gathered:
    results: tuple[Reading, ...]
    peak_in_flight: int
    in_use_after: int


def _slow(build: Build) -> Build:
    return lambda: build().extra(where=[SLEEP_SQL])


async def gathered_reads(build: Build) -> Gathered:
    """`acount()`, `afirst()` and `aexists()` of three QuerySets at once, each holding its
    statement for `SLEEP_SECONDS`: all three must be in flight together on separate connections."""
    tasks = asyncio.gather(
        aq(_slow(build)()).acount(),
        aq(_slow(build)()).afirst(),
        aq(_slow(build)()).aexists(),
    )

    async def all_in_flight() -> bool:
        return registry.in_flight(ALIAS) >= 3

    try:
        await wait_until(all_in_flight, within=10.0)
    except BaseException:
        tasks.cancel()  # never leave the statements running behind a failed wait
        await asyncio.gather(tasks, return_exceptions=True)
        raise
    peak = registry.in_flight(ALIAS)
    count, first, exists = await tasks
    await wait_pool_settled()
    results = tuple(Reading(snapshot_result(value), None) for value in (count, first, exists))
    return Gathered(results, peak, pool_in_use())


@dataclass(frozen=True)
class Cancelled:
    cancelled: bool
    in_flight_after: int
    in_use_after: int


async def _cancel_in_flight(operation: Callable[[], Any]) -> Cancelled:
    """Run `operation()` as a task, cancel it once its statement runs on the server, then report."""
    task = asyncio.ensure_future(operation())

    async def started() -> bool:
        return registry.in_flight(ALIAS) >= 1

    await wait_until(started, within=10.0)
    task.cancel()
    await asyncio.wait([task])
    await wait_pool_settled()
    return Cancelled(task.cancelled(), registry.in_flight(ALIAS), pool_in_use())


async def cancel_read_in_flight(build: Build, op: str) -> Cancelled:
    """Cancel an `<op>()` (`acount`, `afirst`, `aexists`; `aget` with no arguments) while its
    statement runs on the server."""
    return await _cancel_in_flight(lambda: getattr(aq(_slow(build)()), op)())


async def cancel_locking_aget_in_a_pin(build: Build) -> Cancelled:
    """Cancel an `aget()` of a `select_for_update()` QuerySet inside `pin()` while its statement
    runs on the server: leaving the pin must release the pinned connection."""

    async def locking_aget() -> Any:
        async with pin(ALIAS):
            return await aq(_slow(build)().select_for_update()).aget()

    return await _cancel_in_flight(locking_aget)


async def cancel_acount_in_flight(build: Build) -> Cancelled:
    """Cancel an `acount()` while its statement runs on the server."""
    return await cancel_read_in_flight(build, "acount")


async def statements_of(build: Build, op: str) -> list[str]:
    """Executor calls made by one `<op>()` (no arguments) of `build()`."""
    with recorded_statements() as log:
        await getattr(aq(build()), op)()
    return list(log)


async def sync_trio(build: Build) -> tuple[Reading, ...]:
    return (
        await sync_reading(lambda: _sync_call(build, "acount", (), {})),
        await sync_reading(lambda: _sync_call(build, "afirst", (), {})),
        await sync_reading(lambda: _sync_call(build, "aexists", (), {})),
    )


async def gathered_aget_and_iteration(build: Build, pk: int) -> tuple[Reading, Reading]:
    """`aget(pk=pk)` and an `async for` pass over `build()` at once, as snapshots."""
    got, rows = await asyncio.gather(aq(build()).aget(pk=pk), collect(aq(build())))
    return Reading(snapshot_result(got), None), Reading(snapshot_result(rows), None)


async def sync_aget_and_list(build: Build, pk: int) -> tuple[Reading, Reading]:
    """The sync `get(pk=pk)` and `list()` of `build()`, as snapshots."""
    return (
        await sync_reading(lambda: _sync_call(build, "aget", (), {"pk": pk})),
        await sync_reading(lambda: _sync_call(build, "alist", (), {})),
    )


async def compare_aobjects(model: Any, op: str, **kwargs: Any) -> Pair:
    """`model.objects.<sync op>(**kwargs)` against `await model.aobjects.<op>(**kwargs)`."""
    sync = await sync_reading(lambda: _sync_call(model.objects.all, op, (), kwargs))
    async_ = await async_reading(lambda: getattr(model.aobjects, op)(**kwargs))
    return Pair(sync, async_)


async def compare_chained_aobjects(model: Any, op: str, **filters: Any) -> Pair:
    """`model.objects.filter(**filters)` against `model.aobjects.filter(**filters)`, both read by `op`."""
    sync = await sync_reading(
        lambda: _sync_call(partial(model.objects.filter, **filters), op, (), {})
    )
    async_ = await async_reading(lambda: getattr(model.aobjects.filter(**filters), op)())
    return Pair(sync, async_)


@dataclass(frozen=True)
class ConnectionAttempts:
    """What one read did: calls into Django's sync connect paths, and statements it executed (a
    read that ran no statement would pass the first count for the wrong reason)."""

    sync_connects: int
    statements: int


async def sync_connection_attempts(
    build: Build, op: str, *args: Any, **kwargs: Any
) -> ConnectionAttempts:
    """Calls into Django's sync connect paths and executor statements during one `<op>()` of
    `build()`; an error of the read itself (`DoesNotExist`, a refused `get()`) is not the point
    here and is swallowed, but a configuration error (a backend without a driver yet) is not."""
    with (
        count_sync_connection_attempts() as counter,
        recorded_statements() as log,
        contextlib.suppress(
            ObjectDoesNotExist, MultipleObjectsReturned, NotSupportedError, TypeError
        ),
    ):
        await getattr(aq(build()), op)(*args, **kwargs)
    return ConnectionAttempts(counter.count, len(log))


async def iteration_sync_connection_attempts(build: Build) -> ConnectionAttempts:
    """Calls into Django's sync connect paths and executor statements during one `async for` over
    `build()`."""
    with count_sync_connection_attempts() as counter, recorded_statements() as log:
        await collect(aq(build()))
    return ConnectionAttempts(counter.count, len(log))
