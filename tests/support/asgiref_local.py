"""Scenarios around ``asgiref.local.Local`` (ADR-0008, ledger ``[[dependency]]`` asgiref).

``compile_scope`` swaps Django's wrapper into a ``Local(thread_critical=True)``. These helpers
build a fresh ``Local`` per scenario and report what a given observer sees, so the drift tests can
pin the semantics without control flow: thread-local without a running loop, contextvar-backed
inside one (visible to awaited code and to tasks created afterwards, invisible to the parent,
siblings and every other thread, ``sync_to_async`` threads included).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from asgiref.local import Local
from asgiref.sync import sync_to_async

VALUE = "owner-value"
ABSENT = object()


def make_local(*, thread_critical: bool) -> Local:
    return Local(thread_critical=thread_critical)


def read(local: Local) -> Any:
    """The stored value, or ``ABSENT`` when this observer sees none."""
    return getattr(local, "value", ABSENT)


def flag_selects_a_different_storage() -> bool:
    """Whether ``thread_critical`` picks another implementation (the class is private to asgiref)."""
    return type(make_local(thread_critical=True)) is not type(make_local(thread_critical=False))


def sync_same_thread_view() -> Any:
    """No running loop: set, then read in the same thread."""
    local = make_local(thread_critical=True)
    local.value = VALUE
    return read(local)


def sync_other_thread_view() -> Any:
    """No running loop: set here, read from a plain thread."""
    local = make_local(thread_critical=True)
    local.value = VALUE
    return _plain_thread_read(local)


async def awaited_view() -> Any:
    """Set in a coroutine, read by code it awaits."""
    local = make_local(thread_critical=True)
    local.value = VALUE
    return await _read_async(local)


async def later_task_view() -> Any:
    """Set first, then read from a task created afterwards."""
    local = make_local(thread_critical=True)
    local.value = VALUE
    return await asyncio.create_task(_read_async(local))


async def earlier_task_view() -> Any:
    """A task created before the value is set does not see it."""
    local = make_local(thread_critical=True)
    ready, release = asyncio.Event(), asyncio.Event()

    async def early() -> Any:
        ready.set()
        await release.wait()
        return read(local)

    task = asyncio.create_task(early())
    await ready.wait()
    local.value = VALUE
    release.set()
    return await task


async def child_set_views() -> tuple[Any, Any]:
    """(parent's view, child's own view) while a child task holds a value it set."""
    local = make_local(thread_critical=True)
    stored, release = asyncio.Event(), asyncio.Event()

    async def child() -> Any:
        local.value = VALUE
        stored.set()
        await release.wait()
        return read(local)

    task = asyncio.create_task(child())
    await stored.wait()
    parent_view = read(local)
    release.set()
    return parent_view, await task


async def sibling_view_of_child_value() -> Any:
    """What a sibling task sees while another task holds a value it set."""
    local = make_local(thread_critical=True)
    stored, release = asyncio.Event(), asyncio.Event()

    async def setter() -> None:
        local.value = VALUE
        stored.set()
        await release.wait()

    async def observer() -> Any:
        await stored.wait()
        seen = read(local)
        release.set()
        return seen

    _, seen = await asyncio.gather(setter(), observer())
    return seen


async def to_thread_view() -> Any:
    local = make_local(thread_critical=True)
    local.value = VALUE
    return await asyncio.to_thread(read, local)


async def sync_to_async_view(*, thread_sensitive: bool, thread_critical: bool = True) -> Any:
    local = make_local(thread_critical=thread_critical)
    local.value = VALUE
    return await sync_to_async(read, thread_sensitive=thread_sensitive)(local)


async def plain_thread_view() -> Any:
    local = make_local(thread_critical=True)
    local.value = VALUE
    return _plain_thread_read(local)


async def _read_async(local: Local) -> Any:
    return read(local)


def _plain_thread_read(local: Local) -> Any:
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(read(local))
        except BaseException as exc:  # re-raised in the caller, not left as a thread warning
            box.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if isinstance(box[0], BaseException):
        raise box[0]
    return box[0]
