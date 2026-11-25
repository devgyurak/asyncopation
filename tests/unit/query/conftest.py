"""Tripwires for the wrapper unit tests: no SQL executes and Django never hops to a thread."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


def _forbidden(what: str) -> Callable[..., Any]:
    def tripwire(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected {what}")

    return tripwire


@pytest.fixture
def no_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt to execute SQL through Django's compiler fails the test."""
    monkeypatch.setattr(
        "django.db.models.sql.compiler.SQLCompiler.execute_sql", _forbidden("SQL execution")
    )


@pytest.fixture
def no_thread_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Django's own `sync_to_async` (behind acreate, aget, ...) fails the test when reached."""
    monkeypatch.setattr("django.db.models.query.sync_to_async", _forbidden("sync_to_async"))
