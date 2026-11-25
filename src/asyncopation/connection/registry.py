"""Process-global registry (ADR-0004 Decision 6): drivers by ``(alias, loop)``, server info by alias.

Never a contextvar and never filled at import. The registry is synchronous bookkeeping: it opens
nothing (see ``lifecycle``). Entries are keyed by the running loop, so a second loop never sees
another loop's pool.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from asyncopation.compile.server_info import ServerInfo
from asyncopation.connection.engines import Engine
from asyncopation.drivers.base import AsyncDriver, PoolStats

type Key = tuple[str, asyncio.AbstractEventLoop]

_ZERO = PoolStats(0, 0, 0)


@dataclass(frozen=True)
class AliasConfig:
    """The configure-phase result of one alias; ``engine`` is ``None`` for an unsupported ENGINE."""

    engine_name: str
    settings_dict: dict[str, Any]
    use_tz: bool
    time_zone: str
    engine: Engine | None = field(default=None, compare=False)
    factory: Callable[[], AsyncDriver] | None = field(default=None, compare=False)


class Entry:
    """One ``(alias, loop)``. Created inside that loop, so its lock belongs to it (rule 5)."""

    __slots__ = ("closing", "driver", "lock", "warned")

    def __init__(self) -> None:
        self.driver: AsyncDriver | None = None
        self.lock = asyncio.Lock()
        self.warned = False
        self.closing = False


class _Flight:
    """In-flight statements of one ``(alias, loop)``; ``idle`` is set while the count is zero."""

    __slots__ = ("count", "idle")

    def __init__(self) -> None:
        self.count = 0
        self.idle = asyncio.Event()
        self.idle.set()


_entries: dict[Key, Entry] = {}
_server_info: dict[str, ServerInfo] = {}
_configs: dict[str, AliasConfig] = {}
_closing_loops: set[asyncio.AbstractEventLoop] = set()
_flights: dict[Key, _Flight] = {}


# -- configuration ---------------------------------------------------------------------------
def configs() -> dict[str, AliasConfig]:
    return _configs


def set_configs(new: dict[str, AliasConfig]) -> None:
    """Replace the configuration; drop the server info of every alias whose config changed."""
    for alias in list(_server_info):
        if new.get(alias) != _configs.get(alias):
            del _server_info[alias]
    _configs.clear()
    _configs.update(new)


# -- drivers ---------------------------------------------------------------------------------
def entry(alias: str, loop: asyncio.AbstractEventLoop) -> Entry | None:
    return _entries.get((alias, loop))


def entry_or_create(alias: str, loop: asyncio.AbstractEventLoop) -> Entry:
    key = (alias, loop)
    found = _entries.get(key)
    if found is None:
        found = _entries[key] = Entry()  # no await between check and set: single-threaded loop
    return found


def entries() -> list[Key]:
    """Keys that hold an open driver."""
    return [key for key, found in list(_entries.items()) if found.driver is not None]


def is_empty() -> bool:
    """No open driver in any loop (server info and configuration are not drivers)."""
    return not entries()


def is_idle() -> bool:
    """No open driver and no open in progress (``configure()`` may replace the configuration)."""
    return all(
        found.driver is None and not found.lock.locked() for found in list(_entries.values())
    )


def pool_stats(alias: str, loop: asyncio.AbstractEventLoop | None = None) -> PoolStats:
    """Read-only pool counters of ``(alias, loop)`` (the running loop by default); zeros if none."""
    found = _entries.get((alias, loop or asyncio.get_running_loop()))
    driver = found.driver if found is not None else None
    stats = getattr(driver, "pool_stats", None)
    return stats() if stats is not None else _ZERO


# -- server info -----------------------------------------------------------------------------
def server_info(alias: str) -> ServerInfo | None:
    return _server_info.get(alias)


def publish_server_info(alias: str, info: ServerInfo) -> ServerInfo:
    """Store the first successful probe of ``alias``; later callers get the stored winner."""
    return _server_info.setdefault(alias, info)


# -- closing ---------------------------------------------------------------------------------
def begin_closing(loop: asyncio.AbstractEventLoop) -> list[Entry]:
    _closing_loops.add(loop)
    mine = [found for key, found in list(_entries.items()) if key[1] is loop]
    for found in mine:
        found.closing = True
    return mine


def end_closing(loop: asyncio.AbstractEventLoop) -> None:
    _closing_loops.discard(loop)
    for key in [key for key in list(_entries) if key[1] is loop]:
        _entries.pop(key, None)
    # A flight that still counts a statement (the close timed out) stays, so the statement's
    # own ``statement_finished`` keeps balancing the same counter after a re-open.
    for key in [key for key in list(_flights) if key[1] is loop and _flights[key].count == 0]:
        _flights.pop(key, None)


def is_closing(loop: asyncio.AbstractEventLoop) -> bool:
    return loop in _closing_loops


# -- in-flight statements (used by the executors, 5b) ----------------------------------------
def _flight(alias: str) -> _Flight:
    key = (alias, asyncio.get_running_loop())
    flight = _flights.get(key)
    if flight is None:
        flight = _flights[key] = _Flight()
    return flight


def statement_started(alias: str) -> None:
    flight = _flight(alias)
    flight.count += 1
    flight.idle.clear()


def statement_finished(alias: str) -> None:
    flight = _flights.get((alias, asyncio.get_running_loop()))
    if flight is None:  # the loop was closed meanwhile: nothing to account for
        return
    flight.count = max(flight.count - 1, 0)
    if flight.count == 0:
        flight.idle.set()


def in_flight(alias: str) -> int:
    flight = _flights.get((alias, asyncio.get_running_loop()))
    return 0 if flight is None else flight.count


async def wait_idle(loop: asyncio.AbstractEventLoop) -> None:
    """Wait until no statement of ``loop`` is in flight."""
    while True:  # flights created while waiting count too
        busy = [
            flight
            for key, flight in list(_flights.items())
            if key[1] is loop and not flight.idle.is_set()
        ]
        if not busy:
            return
        await busy[0].idle.wait()


def reset() -> None:
    """Forget everything without closing anything (test teardown helper)."""
    _entries.clear()
    _server_info.clear()
    _configs.clear()
    _closing_loops.clear()
    _flights.clear()
