"""Observe `aq(qs).alist()` from the outside: driver statements and sync connection attempts."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from typing import Any
from unittest import mock

from tests.support.connect_counter import count_sync_connection_attempts
from tests.support.differential import QuerySetFactory

_STATEMENT_METHODS = ("fetch_all", "execute", "execute_many", "stream")


def _logging(name: str, original: Callable[..., Any], log: list[str]) -> Callable[..., Any]:
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        log.append(name)
        return original(self, *args, **kwargs)

    return call


@contextlib.contextmanager
def recorded_statements() -> Generator[list[str]]:
    """Names of the executor calls (one per driver statement or stream) made while active."""
    from asyncopation.connection.executor import BorrowedExecutor, PinnedExecutor

    log: list[str] = []
    with contextlib.ExitStack() as stack:
        for cls in (BorrowedExecutor, PinnedExecutor):
            for name in _STATEMENT_METHODS:
                stack.enter_context(
                    mock.patch.object(cls, name, _logging(name, getattr(cls, name), log))
                )
        yield log


async def alist_statements(build: QuerySetFactory) -> list[str]:
    """Executor calls made by one `alist()` of `build()`."""
    from asyncopation import aq

    with recorded_statements() as log:
        await aq(build()).alist()
    return list(log)


async def alist_row_count_and_statements(build: QuerySetFactory) -> tuple[int, int]:
    from asyncopation import aq

    with recorded_statements() as log:
        rows = await aq(build()).alist()
    return len(rows), len(log)


async def alist_sync_connection_attempts(build: QuerySetFactory) -> int:
    """Calls into Django's sync connect paths during one `alist()` (the tripwire also raises)."""
    from asyncopation import aq

    with count_sync_connection_attempts() as counter:
        await aq(build()).alist()
    return counter.count
