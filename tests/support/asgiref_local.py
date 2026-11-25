"""Scenarios around ``asgiref.local.Local`` (ADR-0008, ledger ``[[dependency]]`` asgiref).

``compile_scope`` swaps Django's wrapper into a ``Local(thread_critical=True)``. These helpers
build a fresh ``Local`` per scenario and report what a given observer sees, so the drift tests can
pin the semantics without control flow: thread-local without a running loop, contextvar-backed
inside one (visible to awaited code and to tasks created afterwards, invisible to the parent,
siblings and every other thread, ``sync_to_async`` threads included).
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from typing import Any

from asgiref.local import Local
from asgiref.sync import sync_to_async

VALUE = "owner-value"
OUTER = "outer-value"
INNER = "inner-value"
ABSENT = object()


def make_local(*, thread_critical: bool) -> Local:
    return Local(thread_critical=thread_critical)


def read(local: Local) -> Any:
    """The stored value, or ``ABSENT`` when this observer sees none."""
    return getattr(local, "value", ABSENT)


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
    try:
        await ready.wait()
        local.value = VALUE
    finally:
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
    try:
        await stored.wait()
        parent_view = read(local)
    finally:
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
        try:
            await stored.wait()
            return read(local)
        finally:
            release.set()

    _, seen = await asyncio.gather(setter(), observer())
    return seen


async def child_overwrite_views() -> tuple[Any, Any]:
    """(parent's view, sibling's view) after a child overwrote a value the parent had set.

    The storage is copy-on-write: a write in the child must never reach the parent or a sibling
    that started from the same dict (``compile_scope`` swaps over an existing wrapper).
    """
    local = make_local(thread_critical=True)
    local.value = OUTER
    stored, release = asyncio.Event(), asyncio.Event()

    async def child() -> None:
        local.value = INNER
        stored.set()
        await release.wait()

    async def sibling() -> Any:
        try:
            await stored.wait()
            return read(local)
        finally:
            release.set()

    _, sibling_view = await asyncio.gather(child(), sibling())
    return read(local), sibling_view


async def child_delete_parent_view() -> Any:
    """What the parent still sees after a child task deleted the value both started with."""
    local = make_local(thread_critical=True)
    local.value = OUTER

    async def child() -> None:
        del local.value

    await asyncio.create_task(child())
    return read(local)


def delete_missing_error() -> str:
    """Class name raised by deleting an attribute that was never set (``compile_scope`` relies on it)."""
    local = make_local(thread_critical=True)
    try:
        del local.value
    except BaseException as exc:
        return type(exc).__name__
    return "none"


async def inherited_context_thread_view() -> Any:
    """A thread started with a copy of this context and its own loop does not see the value.

    Each thread has its own contextvar inside a thread-critical ``Local``, so a context a thread
    inherited (3.14's ``thread_inherit_context``, or ``copy_context().run`` here) is not read.
    """
    local = make_local(thread_critical=True)
    local.value = VALUE
    context = contextvars.copy_context()
    box: list[Any] = []

    def target() -> None:
        try:
            box.append(context.run(asyncio.run, _read_async(local)))
        except BaseException as exc:
            box.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if isinstance(box[0], BaseException):
        raise box[0]
    return box[0]


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
