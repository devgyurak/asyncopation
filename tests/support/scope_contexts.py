"""Exiting a compile scope from another contextvars Context (ADR-0008, Decision 1)."""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import Any

from asyncopation.compile.scope import compile_scope
from asyncopation.compile.wrapper import AUTOCOMMIT
from tests.support import connection_entry as entry
from tests.support.compile_wrappers import make_distinct_wrapper, make_wrapper


@dataclass(frozen=True)
class ForeignExit:
    suppressed: Any
    raised: BaseException | None
    peek_after: Any
    autocommit_after: bool


def exit_in_another_context(alias: str, error: BaseException | None, previous: bool) -> ForeignExit:
    """Plain thread, no loop: enter in Context A, exit (optionally with `error`) in Context B.

    Context A starts with autocommit `previous`; Context B holds the opposite value, so only a
    real restore of `previous` can produce the expected result.
    """
    scope = compile_scope(alias, make_wrapper(alias), autocommit=not previous)
    entering, leaving = contextvars.Context(), contextvars.Context()
    entering.run(AUTOCOMMIT.set, previous)
    entering.run(scope.__enter__)
    leaving.run(AUTOCOMMIT.set, not previous)
    triple = (type(error), error, error.__traceback__) if error else (None, None, None)
    suppressed, raised = None, None
    try:
        suppressed = leaving.run(scope.__exit__, *triple)
    except BaseException as exc:
        raised = exc
    return ForeignExit(suppressed, raised, entry.peek(alias), leaving.run(AUTOCOMMIT.get))


@dataclass(frozen=True)
class LoopForeignExit:
    error_name: str
    suppressed: Any
    peek_after_attempt: Any
    autocommit_after_attempt: bool
    peek_after_own_exit: Any
    autocommit_after_own_exit: bool
    own_wrapper: Any


async def foreign_exit_in_loop(alias: str, error: BaseException | None = None) -> LoopForeignExit:
    """Task A holds its own scope while it tries to exit a scope entered in another Context."""
    own, other = make_wrapper(alias), make_distinct_wrapper(alias)
    foreign = compile_scope(alias, other, autocommit=True)
    contextvars.Context().run(foreign.__enter__)
    triple = (type(error), error, error.__traceback__) if error else (None, None, None)

    async def task_a() -> LoopForeignExit:
        name, suppressed = "none", None
        with compile_scope(alias, own, autocommit=False):
            try:
                suppressed = foreign.__exit__(*triple)
            except BaseException as exc:
                name = type(exc).__name__
            during = (entry.peek(alias), AUTOCOMMIT.get())
        return LoopForeignExit(name, suppressed, *during, entry.peek(alias), AUTOCOMMIT.get(), own)

    return await asyncio.create_task(task_a())


async def foreign_generator_close_in_loop(alias: str) -> LoopForeignExit:
    """Task A holds its own scope while it closes the generator of a scope entered elsewhere."""
    own, other = make_wrapper(alias), make_distinct_wrapper(alias)
    foreign = compile_scope(alias, other, autocommit=True)
    contextvars.Context().run(foreign.__enter__)

    async def task_a() -> LoopForeignExit:
        name = "none"
        with compile_scope(alias, own, autocommit=False):
            try:
                foreign.gen.close()  # type: ignore[attr-defined]
            except BaseException as exc:
                name = type(exc).__name__
            during = (entry.peek(alias), AUTOCOMMIT.get())
        return LoopForeignExit(name, None, *during, entry.peek(alias), AUTOCOMMIT.get(), own)

    return await asyncio.create_task(task_a())
