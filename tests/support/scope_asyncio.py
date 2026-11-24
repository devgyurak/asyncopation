"""asyncio / asgiref visibility and cancellation scenarios around `compile_scope`."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from asyncopation.compile.scope import compile_scope
from tests.support import connection_entry as boundary
from tests.support.compile_wrappers import make_wrapper

_SENTINEL = object()


class Marker:
    """Stand-in for a wrapper when only handler semantics are under test."""


async def child_install_visibility(alias: str) -> tuple[Any, Any]:
    """(parent's peek, child's peek) while a child task holds an installed marker."""
    marker = Marker()
    installed, release = asyncio.Event(), asyncio.Event()

    async def child() -> Any:
        boundary.install(alias, marker)
        installed.set()
        await release.wait()
        seen = boundary.peek(alias)
        boundary.remove(alias)
        return seen

    task = asyncio.create_task(child())
    await installed.wait()
    parent_view = boundary.peek(alias)
    release.set()
    return parent_view, await task


async def sibling_install_visibility(alias: str) -> Any:
    """What a sibling task sees while another task holds an installed marker."""
    installed, release = asyncio.Event(), asyncio.Event()

    async def installer() -> None:
        boundary.install(alias, Marker())
        installed.set()
        await release.wait()
        boundary.remove(alias)

    async def observer() -> Any:
        await installed.wait()
        seen = boundary.peek(alias)
        release.set()
        return seen

    _, seen = await asyncio.gather(installer(), observer())
    return seen


async def later_task_sees_parent_swap(alias: str) -> tuple[Any, Any]:
    """(installed marker, what a task created afterwards sees)."""
    marker = Marker()
    boundary.install(alias, marker)
    try:
        seen = await asyncio.create_task(_peek_async(alias))
    finally:
        boundary.remove(alias)
    return marker, seen


async def _peek_async(alias: str) -> Any:
    return boundary.peek(alias)


async def to_thread_sees_parent_swap(alias: str) -> Any:
    boundary.install(alias, Marker())
    try:
        return await asyncio.to_thread(boundary.peek, alias)
    finally:
        boundary.remove(alias)


def plain_thread_sees_parent_swap(alias: str) -> Any:
    box: list[Any] = [_SENTINEL]
    boundary.install(alias, Marker())
    try:
        thread = threading.Thread(target=lambda: box.__setitem__(0, boundary.peek(alias)))
        thread.start()
        thread.join()
    finally:
        boundary.remove(alias)
    return box[0]


def book_for_update_query() -> Any:
    from harness.catalog.models import Book

    return Book.objects.select_for_update().order_by("pk").query


def compile_outcome(query: Any, alias: str) -> tuple[str | None, bool]:
    """(error class name or None, whether the SQL ends up with FOR UPDATE)."""
    try:
        sql, _ = query.get_compiler(using=alias).as_sql()
    except Exception as exc:
        return type(exc).__name__, False
    return None, "FOR UPDATE" in sql


async def compile_in_task(alias: str, wrapper: Any, autocommit: bool) -> tuple[str | None, bool]:
    """Yield to the loop only outside the scope, then compile a select_for_update query."""
    query = book_for_update_query()
    await asyncio.sleep(0)
    with compile_scope(alias, wrapper, autocommit=autocommit):
        outcome = compile_outcome(query, alias)
    await asyncio.sleep(0)
    return outcome


async def gather_mixed_autocommit(alias: str) -> list[tuple[str | None, bool]]:
    wrapper = make_wrapper(alias)
    return list(
        await asyncio.gather(
            compile_in_task(alias, wrapper, True), compile_in_task(alias, wrapper, False)
        )
    )


async def cancel_after_scope(alias: str, wrapper: Any) -> tuple[Any, Any]:
    """Cancel a task that already left its scope: (peek in the task, peek in the parent)."""
    left, seen = asyncio.Event(), []

    async def work() -> None:
        with compile_scope(alias, wrapper, autocommit=True):
            pass
        left.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen.append(boundary.peek(alias))
            raise

    task = asyncio.create_task(work())
    await left.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return seen[0], boundary.peek(alias)


async def cancel_before_scope(alias: str, wrapper: Any) -> tuple[Any, Any]:
    """Cancel a task awaiting before it enters a scope: (peek in the task, peek in the parent)."""
    waiting, seen = asyncio.Event(), []

    async def work() -> None:
        try:
            waiting.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            seen.append(boundary.peek(alias))
            raise
        with compile_scope(alias, wrapper, autocommit=True):
            pass

    task = asyncio.create_task(work())
    await waiting.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return seen[0], boundary.peek(alias)


async def bystander_view(alias: str) -> tuple[Any, Any]:
    """A task created before a scope runs elsewhere: (peek before, peek after) the other scope."""
    wrapper = make_wrapper(alias)
    started, scope_done = asyncio.Event(), asyncio.Event()

    async def bystander() -> tuple[Any, Any]:
        before = boundary.peek(alias)
        started.set()
        await scope_done.wait()
        return before, boundary.peek(alias)

    async def scoper() -> None:
        await started.wait()
        with compile_scope(alias, wrapper, autocommit=True):
            pass
        scope_done.set()

    seen, _ = await asyncio.gather(bystander(), scoper())
    return seen


async def cancelled_inside_scope(alias: str, wrapper: Any) -> tuple[Any, Any]:
    """The scope body raises CancelledError: (peek in the task afterwards, peek in the parent)."""
    seen: list[Any] = []

    async def work() -> None:
        try:
            with compile_scope(alias, wrapper, autocommit=True):
                raise asyncio.CancelledError
        finally:
            seen.append(boundary.peek(alias))

    await asyncio.gather(asyncio.create_task(work()), return_exceptions=True)
    return seen[0], boundary.peek(alias)
