"""Run `ops.compose_sql` on the compile-only wrapper and on Django's live wrapper."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.db import connections

from tests.support.compile_wrappers import make_wrapper


def compose_compile_only(alias: str, sql: str, params: Sequence[Any] | Mapping[str, Any]) -> str:
    return make_wrapper(alias).ops.compose_sql(sql, params)


def compose_live(sql: str, params: Sequence[Any]) -> str:
    return connections["default"].ops.compose_sql(sql, params)


def compose_error(
    alias: str, sql: str, params: Sequence[Any] | Mapping[str, Any], fragment: str
) -> tuple[str, bool]:
    """(error class name, whether its message mentions `fragment`) of the compile-only compose."""
    try:
        make_wrapper(alias).ops.compose_sql(sql, params)
    except Exception as exc:
        return type(exc).__name__, fragment in str(exc)
    return "none", False


def live_error_name(sql: str, params: Sequence[Any]) -> str:
    try:
        connections["default"].ops.compose_sql(sql, params)
    except Exception as exc:
        return type(exc).__name__
    return "none"


def rejection_note(alias: str, value: object) -> str:
    """How `value` is rejected: "exact-type note", "no note", or "not rejected"."""
    try:
        make_wrapper(alias).ops.compose_sql("%s", (value,))
    except TypeError as exc:
        return "exact-type note" if "only exact built-in types" in str(exc) else "no note"
    return "not rejected"
