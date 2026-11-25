"""Synchronous `compile_scope` scenarios (ADR-0008); control flow lives here, not in tests."""

from __future__ import annotations

import threading
from typing import Any

from asyncopation.compile.scope import compile_scope
from asyncopation.compile.wrapper import AUTOCOMMIT
from tests.support import connection_entry as boundary
from tests.support.compile_wrappers import make_wrapper


def peek_inside_scope(alias: str, wrapper: Any, autocommit: bool = True) -> Any:
    with compile_scope(alias, wrapper, autocommit=autocommit):
        return boundary.peek(alias)


def failing_scope(alias: str, wrapper: Any, exception: type[BaseException]) -> tuple[str, Any]:
    """Raise inside a scope; return (caught exception class name, peek after the scope)."""
    caught = ""
    try:
        with compile_scope(alias, wrapper, autocommit=True):
            raise exception("boom")
    except BaseException as exc:
        caught = type(exc).__name__
    return caught, boundary.peek(alias)


def call_inside_scope(alias: str, wrapper: Any, method: str, args: tuple[Any, ...] = ()) -> None:
    with compile_scope(alias, wrapper, autocommit=True):
        getattr(wrapper, method)(*args)


def nested_peeks(alias: str, outer: Any, inner: Any) -> list[Any]:
    """Peek during outer, during inner, after inner, after outer."""
    seen: list[Any] = []
    with compile_scope(alias, outer, autocommit=True):
        seen.append(boundary.peek(alias))
        with compile_scope(alias, inner, autocommit=True):
            seen.append(boundary.peek(alias))
        seen.append(boundary.peek(alias))
    seen.append(boundary.peek(alias))
    return seen


def nested_autocommit(alias: str, wrapper: Any) -> list[bool]:
    """get_autocommit() during outer(False), inner(True), after inner, after outer (the default)."""
    seen: list[bool] = []
    with compile_scope(alias, wrapper, autocommit=False):
        seen.append(wrapper.get_autocommit())
        with compile_scope(alias, wrapper, autocommit=True):
            seen.append(wrapper.get_autocommit())
        seen.append(wrapper.get_autocommit())
    seen.append(AUTOCOMMIT.get())
    return seen


def autocommit_in_scope(alias: str, wrapper: Any, autocommit: bool) -> bool:
    with compile_scope(alias, wrapper, autocommit=autocommit):
        return wrapper.get_autocommit()


def autocommit_after_failing_scope(
    alias: str, wrapper: Any, exception: type[BaseException]
) -> tuple[str, bool, bool]:
    """(caught name, autocommit after the failed inner scope, default after the outer exit)."""
    caught, between = "", True
    with compile_scope(alias, wrapper, autocommit=False):
        try:
            with compile_scope(alias, wrapper, autocommit=True):
                raise exception("boom")
        except BaseException as exc:
            caught = type(exc).__name__
        between = wrapper.get_autocommit()
    return caught, between, AUTOCOMMIT.get()


def removed_entry_then_raise(alias: str, wrapper: Any) -> str:
    """The body removes the entry itself and raises ValueError: which exception escapes?"""
    try:
        with compile_scope(alias, wrapper, autocommit=True):
            boundary.remove(alias)
            raise ValueError("boom")
    except BaseException as exc:
        return type(exc).__name__
    return "none"


def thread_race_mismatches(
    alias: str, threads: int = 4, iterations: int = 2000, timeout: float = 30.0
) -> int:
    """Threads (each with its own loop) share one wrapper and alternate autocommit values.

    Returns how many `get_autocommit()` reads inside a scope differed from the thread's own value.
    A barrier inside every scope forces all threads to overlap, with no sleeping. Any thread error
    aborts the barrier (so nobody hangs), is collected and re-raised here; a thread that is still
    alive after `timeout` raises as a hang.
    """
    import asyncio

    wrapper = make_wrapper(alias)
    barrier = threading.Barrier(threads, timeout=timeout)
    mismatches = [0] * threads
    errors: list[BaseException] = []

    def hammer(index: int) -> None:
        value = index % 2 == 0
        try:
            barrier.wait()
            for _ in range(iterations):
                with compile_scope(alias, wrapper, autocommit=value):
                    barrier.wait()  # every thread is inside its own scope before anyone reads
                    mismatches[index] += wrapper.get_autocommit() is not value
        except BaseException as exc:
            errors.append(exc)
            barrier.abort()

    async def work(index: int) -> None:
        hammer(index)

    workers = [
        threading.Thread(target=asyncio.run, args=(work(i),), name=f"race-{i}", daemon=True)
        for i in range(threads)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout)
    hung = [worker.name for worker in workers if worker.is_alive()]
    if hung:
        raise TimeoutError(f"threads still running after {timeout}s: {hung}")
    if errors:
        raise errors[0]
    return sum(mismatches)


def swapped_peeks(alias: str, wrapper: Any) -> tuple[Any, Any]:
    """(entry inside `swapped`, entry afterwards)."""
    from asyncopation._django.connections import swapped

    with swapped(alias, wrapper):
        inside = boundary.peek(alias)
    return inside, boundary.peek(alias)


def swapped_failing(alias: str, wrapper: Any, exception: type[BaseException]) -> tuple[str, Any]:
    from asyncopation._django.connections import swapped

    caught = ""
    try:
        with swapped(alias, wrapper):
            raise exception("boom")
    except BaseException as exc:
        caught = type(exc).__name__
    return caught, boundary.peek(alias)


def swapped_nested(alias: str, outer: Any, inner: Any) -> list[Any]:
    from asyncopation._django.connections import swapped

    seen: list[Any] = []
    with swapped(alias, outer):
        seen.append(boundary.peek(alias))
        with swapped(alias, inner):
            seen.append(boundary.peek(alias))
        seen.append(boundary.peek(alias))
    seen.append(boundary.peek(alias))
    return seen


def swapped_entry_deleted_inside(alias: str, wrapper: Any, raises: bool) -> str:
    """The body deletes the entry itself (and optionally raises): which exception escapes?"""
    from asyncopation._django.connections import swapped

    try:
        with swapped(alias, wrapper):
            boundary.remove(alias)
            if raises:
                raise ValueError("boom")
    except BaseException as exc:
        return type(exc).__name__
    return "none"


def body_error_through_scope(alias: str, wrapper: Any) -> str:
    """The body's exception must reach the caller unchanged (not replaced by a restore error)."""
    try:
        with compile_scope(alias, wrapper, autocommit=True):
            boundary.remove(alias)
            raise KeyError("boom")
    except BaseException as exc:
        return type(exc).__name__
    return "none"


def generator_style_close(alias: str, wrapper: Any) -> tuple[str, bool, bool]:
    """Drive the scope's generator by hand: `next()` to the yield, then `close()` in the same Context.

    Returns (exception name or "none", whether an entry is left, autocommit after the close).
    """
    scope = compile_scope(alias, wrapper, autocommit=False)
    name = "none"
    try:
        next(scope.gen)  # type: ignore[attr-defined]
        scope.gen.close()  # type: ignore[attr-defined]
    except BaseException as exc:
        name = type(exc).__name__
    return name, boundary.has_entry(alias), AUTOCOMMIT.get()
