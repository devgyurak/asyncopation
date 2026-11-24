"""Direct access to Django's per-context connection entry (tests are not under guard A102)."""

from __future__ import annotations

from typing import Any

from django.db import connections


def peek(alias: str) -> Any | None:
    """The wrapper installed for `alias` in the current context, or None (never creates one)."""
    return getattr(connections._connections, alias, None)  # ty: ignore[missing-attribute]


def install(alias: str, wrapper: Any) -> None:
    connections[alias] = wrapper


def remove(alias: str) -> None:
    del connections[alias]


def has_entry(alias: str) -> bool:
    """Whether the current context holds any entry for `alias` (independent of `peek`)."""
    return hasattr(connections._connections, alias)  # ty: ignore[missing-attribute]
