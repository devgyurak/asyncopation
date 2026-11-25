"""Counting tripwires on every sync connect path, including the compile-only overrides."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

from django.db.backends.base.base import BaseDatabaseWrapper

from asyncopation._django.postgresql import BaseWrapper
from asyncopation.compile.postgresql import PostgreSQLCompileWrapper
from asyncopation.compile.wrapper import CompileOnlyConnectionError, CompileOnlyMixin
from tests.support.compile_wrappers import make_distinct_wrapper, make_wrapper

_SYNC_CONNECT_METHODS = (
    "connect",
    "ensure_connection",
    "create_cursor",
    "_cursor",
    "cursor",
    "chunked_cursor",
    "get_new_connection",
)
_MIXIN_METHODS = _SYNC_CONNECT_METHODS


@dataclass
class AttemptCounter:
    count: int = 0
    names: list[str] = field(default_factory=list)


@contextlib.contextmanager
def count_sync_connection_attempts() -> Generator[AttemptCounter]:
    """Count calls to Django's sync connect paths; each attempt also fails loudly.

    Wrap a whole `alist()` (open, compile, execute and materialize) to prove the pipeline never
    reaches a sync connection: see `alist_sync_connection_attempts`.
    """
    counter = AttemptCounter()
    with contextlib.ExitStack() as stack:
        for owner in (BaseDatabaseWrapper, BaseWrapper):
            for name in _SYNC_CONNECT_METHODS:
                stack.enter_context(
                    mock.patch.object(owner, name, _tripwire(counter, name), create=True)
                )
        for owner in (CompileOnlyMixin, PostgreSQLCompileWrapper):
            for name in _MIXIN_METHODS:
                stack.enter_context(
                    mock.patch.object(
                        owner,
                        name,
                        _tripwire(counter, name, CompileOnlyConnectionError),
                        create=True,
                    )
                )
        yield counter


def _tripwire(
    counter: AttemptCounter, name: str, error: type[Exception] = RuntimeError
) -> Callable[..., Any]:
    def attempt(*args: Any, **kwargs: Any) -> Any:
        counter.count += 1
        counter.names.append(name)
        raise error(f"compile-only: sync connection attempt: {name}")

    return attempt


def construct_while_connecting_is_forbidden(alias: str) -> tuple[Any, int]:
    with count_sync_connection_attempts() as counter:
        wrapper = make_distinct_wrapper(alias)
    return wrapper, counter.count


def swallowed_ensure_connection_attempts(alias: str) -> int:
    """Attempts counted when ensure_connection() is called and its error swallowed."""
    wrapper = make_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        contextlib.suppress(CompileOnlyConnectionError),
    ):
        wrapper.ensure_connection()
    return counter.count


def swallowed_cursor_attempts(alias: str) -> int:
    wrapper = make_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        contextlib.suppress(CompileOnlyConnectionError),
    ):
        wrapper._cursor()
    return counter.count


def swallowed_attempts(alias: str, method: str) -> int:
    """Attempts counted when `method()` is called on a compile-only wrapper and its error swallowed."""
    wrapper = make_wrapper(alias)
    with (
        count_sync_connection_attempts() as counter,
        contextlib.suppress(CompileOnlyConnectionError),
    ):
        getattr(wrapper, method)()
    return counter.count
