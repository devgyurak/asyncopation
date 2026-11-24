"""``compile_scope``: make Django's ``connections[alias]`` resolve to the compile-only wrapper.

Contract (ADR-0008, Decision 1-3): every Django call that reads ``connections[...]`` while a
statement is prepared runs inside one scope. The scope is synchronous, nests like a stack, restores
in ``finally`` and contains no ``await``, so no other task can observe the swap and a cancellation
cannot land inside it. It is entered per synchronous prepare phase, never for the lifetime of a
task. The caller (engine) passes ``autocommit = not pinned``; ``compile`` knows nothing about
connections or transactions.

A scope must be entered and exited in the same thread and ``contextvars`` Context (a plain ``with``
block in synchronous code): never inside a generator that yields while the scope is open, and never
closed from another thread or task. Inside a running loop, exiting from another Context is
detected: the connection entry and autocommit of that Context are left alone and
``ScopeContextError`` is raised (an exception from the body takes precedence, except
``GeneratorExit``). Without a running loop (plain threads) the exit is accepted and restores the
previous values, which assumes no other scope of that thread is open in another Context.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager

from asyncopation._django.connections import ScopeContextError
from asyncopation._django.connections import swapped as _swapped
from asyncopation.compile.wrapper import AUTOCOMMIT, CompileOnlyMixin

__all__ = ["ScopeContextError", "compile_scope"]


@contextmanager
def compile_scope[W: CompileOnlyMixin](alias: str, wrapper: W, *, autocommit: bool) -> Generator[W]:
    if wrapper.alias != alias:
        raise ValueError(f"wrapper belongs to alias {wrapper.alias!r}, not {alias!r}")
    previous = AUTOCOMMIT.get()
    # `ContextVar.set` cannot raise, so the token is taken before the try block; it is needed to
    # tell, on exit, whether the scope is closed in the Context that entered it.
    token = AUTOCOMMIT.set(autocommit)
    try:
        with _swapped(alias, wrapper):
            yield wrapper
    finally:
        _restore_autocommit(token, previous)


def _restore_autocommit(token, previous: bool) -> None:
    try:
        AUTOCOMMIT.reset(token)
    except ValueError:
        # Exited from another Context. In a running loop that Context has its own value: leave it
        # (the connection swap raised ScopeContextError); otherwise restore the plain value.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            AUTOCOMMIT.set(previous)
