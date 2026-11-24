"""Driver contract (ADR-0003): protocols and value types shared by every backend driver.

A driver behaves like Django's *sync* backend connection for the same alias - same values, same
``rowcount`` semantics, same session state - and raises ``django.db`` exceptions. SQL shape is
not a driver concern: it comes from the compile-only wrapper's ``features`` (ADR-0003 Decision 3).

Transaction contract. A borrowed connection is in autocommit mode. ``begin()`` leaves autocommit
and opens a transaction (lazily, so the configured isolation level applies); ``commit()`` /
``rollback()`` end it and return to autocommit. A connection returned to the pool inside a
transaction is rolled back first. Policy (pinning, broken transactions) belongs to the
``connection`` / ``transaction`` layers, not to drivers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

type Params = Sequence[Any] | Mapping[str, Any] | None
"""SQL parameters exactly as Django's compiler produces them (``None`` = no parameters)."""


@dataclass(frozen=True)
class Capabilities:
    """Driver/runtime facts. Never SQL shape - that is the compile-only wrapper's ``features``."""

    server_version: tuple[int, ...]
    server_side_cursors: bool  # false under poolers / DISABLE_SERVER_SIDE_CURSORS
    atomic_execute_many: bool  # one failing parameter set leaves no rows behind


@dataclass(frozen=True)
class ExecResult:
    """Outcome of a statement.

    ``rowcount`` follows Django's sync backend (``-1`` when the driver does not know). ``rows`` is
    ``None`` for statements that return no result set and a list of tuples otherwise
    (``RETURNING``). ``lastrowid`` is ``None`` on backends that use ``RETURNING`` (PostgreSQL).
    """

    rowcount: int
    lastrowid: int | None = None
    rows: list[tuple[Any, ...]] | None = None


@dataclass(frozen=True)
class PoolStats:
    """Read-only pool counters (ADR-0004 Decision 6): connections in use, idle and waiting.

    ``in_use`` is the pool's size minus its idle connections, so it also counts connections that
    are still opening or being replaced (a discarded connection is replaced in the background and
    counts as in use until the replacement is idle). Tests therefore wait for the pool to settle.
    """

    in_use: int
    idle: int
    waiting: int


class AsyncDbConnection(Protocol):
    """One borrowed connection: at most one statement or stream in flight (ADR-0001)."""

    async def fetch_all(self, sql: str, params: Params) -> list[tuple[Any, ...]]: ...

    async def execute(self, sql: str, params: Params) -> ExecResult: ...

    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult: ...

    def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AbstractAsyncContextManager[AsyncIterator[list[tuple[Any, ...]]]]:
        """Server-side cursor yielding lists of at most ``chunk`` rows; closed on exit."""
        ...

    async def begin(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def savepoint(self, sid: str) -> None: ...

    async def release_savepoint(self, sid: str) -> None: ...

    async def rollback_to_savepoint(self, sid: str) -> None: ...


class AsyncDriver(Protocol):
    """Pool owner for one ``(alias, event loop)`` (ADR-0004). Created without I/O."""

    @property
    def capabilities(self) -> Capabilities:
        """Available while the driver is open; raises ``RuntimeError`` before ``open()`` and after
        ``close()``."""
        ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    def connection(self) -> AbstractAsyncContextManager[AsyncDbConnection]:
        """Borrow a connection; it is returned (or discarded when broken) on exit."""
        ...

    def is_disconnect(self, exc: BaseException) -> bool:
        """True when ``exc`` (raw or mapped) means the connection is gone for good."""
        ...


class SupportsPoolStats(Protocol):
    """Optional: drivers backed by a pool expose its counters for tests and metrics."""

    def pool_stats(self) -> PoolStats: ...
