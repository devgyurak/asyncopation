"""Tripwires for the wrapper unit tests: no SQL executes and Django never hops to a thread."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

from tests.support.aq_wrapper import forbid_executor_statements


def _forbidden(what: str) -> Callable[..., Any]:
    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected {what}")

    return tripwire


@pytest.fixture
def no_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to execute SQL fails the test: Django's compiler and our async executors."""
    monkeypatch.setattr(
        "django.db.models.sql.compiler.SQLCompiler.execute_sql", _forbidden("SQL execution")
    )
    forbid_executor_statements(monkeypatch, _forbidden("async SQL execution"))


@pytest.fixture
def no_thread_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Django's own `sync_to_async` (behind acreate, aget, ...) fails the test when reached."""
    monkeypatch.setattr("django.db.models.query.sync_to_async", _forbidden("sync_to_async"))


@pytest.fixture
def no_async_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Our async executors fail the test when asked for a statement (Django's sync compiler is
    left alone, so its own refusal in an async context is what a test sees)."""
    forbid_executor_statements(monkeypatch, _forbidden("async SQL execution"))


@pytest.fixture
def no_sync_to_async(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling any asgiref `SyncToAsync` (the thread hop behind every Django `a*` coroutine) fails
    the test."""
    # Marked as a coroutine function, as the `async def __call__` it replaces: the `partial` that
    # `SyncToAsync.__get__` hands out must still look like one to `inspect.iscoroutinefunction`.
    tripwire = inspect.markcoroutinefunction(_forbidden("SyncToAsync call"))
    monkeypatch.setattr("asgiref.sync.SyncToAsync.__call__", tripwire)
