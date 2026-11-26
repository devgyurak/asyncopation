"""Observers that record what they are given, and views of the recorded events (ADR-0005)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

import pytest

STARTED = "query_started"
FINISHED = "query_finished"
BOTH = [STARTED, FINISHED]


def hooks() -> Any:
    """`asyncopation.connection.hooks`, imported when a scenario needs it."""
    from asyncopation.connection import hooks as module

    return module


class Recorder:
    """A synchronous observer that keeps every event it is given, in order."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self, event: Any) -> None:
        self.events.append(event)

    @property
    def names(self) -> list[str]:
        return [event.name for event in self.events]

    @property
    def started(self) -> list[Any]:
        return [event for event in self.events if event.name == STARTED]

    @property
    def finished(self) -> list[Any]:
        return [event for event in self.events if event.name == FINISHED]

    @property
    def sqls(self) -> list[str]:
        """The SQL of every statement that started."""
        return [event.sql for event in self.started]

    @property
    def only_started(self) -> Any:
        return _only(self.started, STARTED)

    @property
    def only_finished(self) -> Any:
        return _only(self.finished, FINISHED)

    @property
    def shapes(self) -> list[tuple[Any, ...]]:
        """`(name, alias, kind, sql, params, many)` of every event."""
        return [
            (event.name, event.alias, event.kind, event.sql, event.params, event.many)
            for event in self.events
        ]

    @property
    def statements(self) -> list[tuple[Any, Any]]:
        """`(sql, params)` of every started event."""
        return [(event.sql, event.params) for event in self.started]

    @property
    def finished_statements(self) -> list[tuple[Any, Any]]:
        return [(event.sql, event.params) for event in self.finished]

    @property
    def kinds_and_sqls(self) -> list[tuple[str, str]]:
        """`(kind, sql)` of every started event."""
        return [(event.kind, event.sql) for event in self.started]

    @property
    def finished_pairs(self) -> list[tuple[str, Any]]:
        """`(sql, rowcount)` of every finished event, in the order they finished."""
        return [(event.sql, event.rowcount) for event in self.finished]

    @property
    def aliases(self) -> list[str]:
        return [event.alias for event in self.events]

    @property
    def params_seen(self) -> list[Any]:
        return [event.params for event in self.events]


def _only(events: list[Any], name: str) -> Any:
    if len(events) != 1:
        raise AssertionError(f"expected exactly one {name} event, got {len(events)}: {events!r}")
    return events[0]


class ObserverFailure(Exception):
    """What `Failing` raises."""


class Failing:
    """An observer that raises `ObserverFailure` on one event name and counts its calls."""

    def __init__(self, on: str) -> None:
        self.on = on
        self.calls = 0

    def __call__(self, event: Any) -> None:
        self.calls += 1
        if event.name == self.on:
            raise ObserverFailure(f"observer failed on {self.on}")


class Tagged:
    """Appends `<tag>:<event name>` to a shared log, so the call order across observers shows."""

    def __init__(self, log: list[str], tag: str) -> None:
        self.log = log
        self.tag = tag

    def __call__(self, event: Any) -> None:
        self.log.append(f"{self.tag}:{event.name}")


async def async_observer(event: Any) -> None:
    """A coroutine function: refused as an observer (observers are synchronous)."""


def field_names(cls: Any) -> list[str]:
    return [field.name for field in dataclasses.fields(cls)]


def logged_failures(caplog: pytest.LogCaptureFixture, logger: str) -> int:
    """How many records of `logger` carry an `ObserverFailure` (the failure of an observer)."""
    return sum(_is_observer_failure(record, logger) for record in caplog.records)


def _is_observer_failure(record: logging.LogRecord, logger: str) -> bool:
    exc_info = record.exc_info
    return record.name == logger and bool(exc_info) and isinstance(exc_info[1], ObserverFailure)


def logged_failure_messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    """The messages of the `ObserverFailure` records of `logger`."""
    return [
        record.getMessage() for record in caplog.records if _is_observer_failure(record, logger)
    ]


def error_messages(caplog: pytest.LogCaptureFixture, logger: str) -> list[str]:
    """The messages `logger` emitted at ERROR level or above."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == logger and record.levelno >= logging.ERROR
    ]


class Raising:
    """An observer that raises `error` (any `BaseException`) on one event name."""

    def __init__(self, on: str, error: BaseException) -> None:
        self.on = on
        self.error = error

    def __call__(self, event: Any) -> None:
        if event.name == self.on:
            raise self.error


class AsyncCall:
    """An instance whose `__call__` is a coroutine function: refused as an observer."""

    async def __call__(self, event: Any) -> None:
        return None


class ReturnsFuture:
    """A plain callable that (wrongly) returns an `asyncio.Future` (an awaitable, not a coroutine)."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, event: Any) -> Any:
        self.names.append(event.name)
        return asyncio.get_running_loop().create_future()


class WithMethod:
    """Observes through a bound method (`obj.on_query`), which is a new object on every access."""

    def __init__(self) -> None:
        self.recorder = Recorder()

    def on_query(self, event: Any) -> None:
        self.recorder(event)


class ReturnsCoroutine:
    """A plain callable that (wrongly) returns a coroutine; records the events it was given."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, event: Any) -> Any:
        self.names.append(event.name)
        return async_observer(event)


def global_observers_left() -> tuple[Any, ...]:
    """The global registrations still present; they are cleared (teardown of a unit test)."""
    module = hooks()
    left = module._global
    module._global = ()
    return left


def started_event(**overrides: Any) -> Any:
    """A `QueryEvent` built by keyword, with the fields a started statement has."""
    values: dict[str, Any] = {
        "name": STARTED,
        "alias": "default",
        "kind": "query",
        "sql": "SELECT 1",
        "params": None,
        "many": False,
        "rowcount": None,
        "error": None,
        "queue_time": 0.0,
        "query_time": None,
        "total_time": None,
    }
    values.update(overrides)
    return hooks().QueryEvent(**values)
