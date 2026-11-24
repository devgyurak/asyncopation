"""PostgreSQL compile-only wrapper (ADR-0008).

Imports Django's PostgreSQL backend, hence psycopg; keep this module out of
``asyncopation.compile``'s eager imports.
"""

from __future__ import annotations

from typing import Any

from asyncopation._django.postgresql import BaseWrapper, CompileOperations, inject_pg_version
from asyncopation.compile.server_info import PostgreSQLServerInfo, ServerInfo
from asyncopation.compile.wrapper import CompileOnlyMixin


class PostgreSQLCompileWrapper(CompileOnlyMixin, BaseWrapper):
    """Django's PostgreSQL wrapper that can generate SQL but never connect."""

    ops_class = CompileOperations
    server_info: PostgreSQLServerInfo | None = None

    def apply_server_info(self, info: ServerInfo) -> None:
        if not isinstance(info, PostgreSQLServerInfo):
            raise TypeError(f"expected PostgreSQLServerInfo, got {type(info).__name__}")
        inject_pg_version(self, info.pg_version)
        self.server_info = info


def _snapshot(value: Any) -> Any:
    """Copy the dict/list structure of a settings value and share every leaf.

    Leaves (an ``ssl.SSLContext``, psycopg's ``AdaptersMap``, callables holding locks) are neither
    copyable nor comparable by value, so they are compared by identity; tuples and sets are leaves
    too, so mutating one in place is not detected, and settings must not contain cycles.
    """
    if isinstance(value, dict):
        return {k: _snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snapshot(v) for v in value]
    return value


_WRAPPERS: dict[str, PostgreSQLCompileWrapper] = {}


def wrapper_for(
    alias: str, settings_dict: dict[str, Any], info: PostgreSQLServerInfo
) -> PostgreSQLCompileWrapper:
    """Return the one shared wrapper of ``alias``, creating it on first use.

    ``settings_dict`` must be the *configured* dict (Django's defaults such as ``TIME_ZONE`` /
    ``OPTIONS`` applied, as ``ConnectionHandler.configure_settings`` does; the engine provides it),
    not a raw ``settings.DATABASES`` entry. The wrapper keeps a copy of the dict/list structure
    (leaf values are shared and compared by identity), so a dict mutated in place later compares
    unequal. A different ``settings_dict`` or ``info`` yields a fresh wrapper that replaces the
    registered one, so cached properties derived from the old server version never go stale.

    Construction is synchronous and performs no I/O. The registry check-then-set is unlocked:
    build wrappers during startup, not concurrently from several threads. Per-scope state
    (autocommit) is set by ``compile_scope`` at entry, never stored here.
    """
    wrapper = _WRAPPERS.get(alias)
    if wrapper is None or wrapper.settings_dict != settings_dict or wrapper.server_info != info:
        wrapper = _WRAPPERS[alias] = PostgreSQLCompileWrapper(_snapshot(settings_dict), alias)
        wrapper.apply_server_info(info)
    return wrapper
