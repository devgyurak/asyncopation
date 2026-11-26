"""`wrapper_for` and the timezone settings: a wrapper caches ``timezone_name`` derived from them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.test import override_settings

from asyncopation.compile import mysql, postgresql
from tests.support.compile_wrappers import PG_SETTINGS, pg_info
from tests.support.mysql_wrappers import MYSQL_SETTINGS, mysql_info

BUILDERS: dict[str, Callable[[str], Any]] = {
    "mysql": lambda alias: mysql.wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info()),
    "postgresql": lambda alias: postgresql.wrapper_for(alias, dict(PG_SETTINGS), pg_info()),
}


def wrappers_across(backend: str, alias: str, **overrides: Any) -> tuple[Any, Any, Any]:
    """The wrapper before, while and after `overrides` of the settings are active."""
    build = BUILDERS[backend]
    before = build(alias)
    with override_settings(**overrides):
        during = build(alias)
    return before, during, build(alias)


def wrappers_across_unchanged(backend: str, alias: str) -> tuple[Any, Any]:
    """Two consecutive lookups with the settings untouched."""
    build = BUILDERS[backend]
    return build(alias), build(alias)


def timezone_name_during(backend: str, alias: str, time_zone: str) -> str:
    """``timezone_name`` of the wrapper looked up while ``TIME_ZONE`` is overridden (no USE_TZ),
    after the wrapper built before the override had read (and so cached) its own."""
    BUILDERS[backend](alias).timezone_name  # noqa: B018 - fills the cached property
    with override_settings(USE_TZ=False, TIME_ZONE=time_zone):
        return BUILDERS[backend](alias).timezone_name
