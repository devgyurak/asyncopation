"""Backend-agnostic half of the compile-only ``DatabaseWrapper`` (ADR-0008).

``CompileOnlyMixin`` is placed *before* a Django wrapper class in the bases so its methods win:
nothing can connect or open a cursor, and ``get_autocommit`` answers from ``AUTOCOMMIT``, a
``ContextVar`` that ``compile_scope`` sets at entry and restores on exit (``select_for_update``
compilation reads it). The state is context-local rather than an attribute because the wrapper
is shared per alias across every thread and task. It imports nothing from Django at module level.

Django code that asks for a live connection while generating SQL fails with
``CompileOnlyConnectionError``; the one known caller, ``ops.compose_sql`` (``contrib.postgres``
``SearchHeadline`` options), is overridden by the backend's operations (``_django.postgresql``) with
a driver-free renderer for the few types it receives.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from asyncopation.compile.server_info import ServerInfo


def snapshot_settings(value: Any) -> Any:
    """Copy the dict/list structure of a settings value and share every leaf.

    Leaves (an ``ssl.SSLContext``, psycopg's ``AdaptersMap``, callables holding locks) are neither
    copyable nor comparable by value, so they are compared by identity; tuples and sets are leaves
    too, so mutating one in place is not detected, and settings must not contain cycles.
    """
    if isinstance(value, dict):
        return {k: snapshot_settings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snapshot_settings(v) for v in value]
    return value


def timezone_key() -> tuple[bool, str]:
    """``(USE_TZ, TIME_ZONE)``: what Django's cached ``timezone`` / ``timezone_name`` derive from.

    Django resets those caches of its real connections when the settings change (a test signal);
    the compile-only wrapper is not in ``connections``, so ``wrapper_for`` compares this key instead.
    It only rebuilds the *wrapper*: a driver session that is already open keeps the time zone it was
    opened with (``Engine.prepare`` reads it once, at ``configure()``; ADR-0008 Decision 5).
    """
    from django.conf import settings

    return bool(settings.USE_TZ), str(settings.TIME_ZONE)


AUTOCOMMIT: ContextVar[bool] = ContextVar("asyncopation_compile_autocommit", default=True)


class CompileOnlyConnectionError(RuntimeError):
    """A compile-only wrapper was asked for a connection."""


class CompileOnlyMixin:
    """Overrides that make a Django ``DatabaseWrapper`` incapable of I/O."""

    alias: str
    timezone_key: tuple[bool, str] | None = None
    """``timezone_key()`` when ``wrapper_for`` built it."""

    def connect(self) -> None:
        raise CompileOnlyConnectionError("compile-only wrapper cannot connect")

    def ensure_connection(self) -> None:
        raise CompileOnlyConnectionError("compile-only wrapper has no connection")

    def _cursor(self, *args: Any, **kwargs: Any) -> Any:
        raise CompileOnlyConnectionError("compile-only wrapper cannot create a cursor")

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        raise CompileOnlyConnectionError("compile-only wrapper cannot create a cursor")

    def chunked_cursor(self, *args: Any, **kwargs: Any) -> Any:
        raise CompileOnlyConnectionError("compile-only wrapper cannot create a cursor")

    def get_new_connection(self, *args: Any, **kwargs: Any) -> Any:
        raise CompileOnlyConnectionError("compile-only wrapper cannot open a connection")

    def create_cursor(self, *args: Any, **kwargs: Any) -> Any:
        raise CompileOnlyConnectionError("compile-only wrapper cannot create a cursor")

    def get_autocommit(self) -> bool:
        return AUTOCOMMIT.get()

    def apply_server_info(self, info: ServerInfo) -> None:
        raise NotImplementedError
