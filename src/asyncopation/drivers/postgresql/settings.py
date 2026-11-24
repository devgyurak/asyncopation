"""Translate a Django ``DATABASES`` entry into psycopg connection settings.

Ported from ``django/db/backends/postgresql/base.py`` (``DatabaseWrapper.get_connection_params``,
``_configure_connection`` inputs) and ``django/db/backends/base/base.py`` (``timezone`` /
``timezone_name``) @ 6.1.3. Pure functions: no I/O, no connection.
"""

from __future__ import annotations

import datetime
import zoneinfo
from dataclasses import dataclass
from typing import Any

import psycopg
from django.core.exceptions import ImproperlyConfigured
from psycopg_pool import AsyncConnectionPool

from asyncopation._django.postgresql import adapters_template

# psycopg_pool defaults are min_size=4 / max_size=min_size; these are provisional until the
# ADR-0004 implementation issue benchmarks pool sizing.
DEFAULT_POOL_OPTIONS: dict[str, Any] = {"min_size": 1, "max_size": 10}

_MAX_NAME_LENGTH = 63  # PostgreSQL identifier limit, Django's ops.max_name_length()


@dataclass(frozen=True)
class PostgresConfig:
    """Everything a ``PostgresDriver`` needs, resolved without I/O."""

    alias: str
    conn_kwargs: dict[str, Any]
    pool_kwargs: dict[str, Any]
    isolation_level: psycopg.IsolationLevel | None  # None: keep the server/role default
    timezone_name: str
    assume_role: str | None
    server_side_binding: bool
    server_side_cursors: bool


def resolve_timezone(
    settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str
) -> tuple[datetime.tzinfo | None, str]:
    """Return ``(tzinfo, name)`` like ``BaseDatabaseWrapper.timezone`` / ``timezone_name``."""
    # Ported from django/db/backends/base/base.py::timezone, timezone_name @ 6.1.3 - one function.
    if not use_tz:
        return None, time_zone
    name = settings_dict.get("TIME_ZONE")
    if name is None:
        return datetime.UTC, "UTC"
    return zoneinfo.ZoneInfo(name), name


def build_config(
    alias: str, settings_dict: dict[str, Any], *, use_tz: bool, time_zone: str
) -> PostgresConfig:
    """Port of ``get_connection_params`` with async cursor classes (see module docstring)."""
    # Changes: OPTIONS is copied instead of mutated; cursor classes are the async psycopg ones.
    if settings_dict["NAME"] == "" and not settings_dict.get("OPTIONS", {}).get("service"):
        raise ImproperlyConfigured(
            "settings.DATABASES is improperly configured. Please supply the NAME or "
            "OPTIONS['service'] value."
        )
    if len(settings_dict["NAME"] or "") > _MAX_NAME_LENGTH:
        raise ImproperlyConfigured(
            f"The database name '{settings_dict['NAME']}' ({len(settings_dict['NAME'])} "
            f"characters) is longer than PostgreSQL's limit of {_MAX_NAME_LENGTH} characters. "
            "Supply a shorter NAME in settings.DATABASES."
        )
    options = dict(settings_dict.get("OPTIONS") or {})
    if settings_dict["NAME"]:
        conn_params: dict[str, Any] = {"dbname": settings_dict["NAME"], **options}
    elif settings_dict["NAME"] is None:
        # Connect to the default 'postgres' db (needed to create/drop databases).
        options.pop("service", None)
        conn_params = {"dbname": "postgres", **options}
    else:
        conn_params = {**options}

    conn_params["client_encoding"] = "UTF8"
    assume_role = conn_params.pop("assume_role", None)
    isolation_level_value = conn_params.pop("isolation_level", None)
    pool_option = conn_params.pop("pool", None)
    server_side_binding = conn_params.pop("server_side_binding", None) is True
    conn_params.setdefault(
        "cursor_factory",
        psycopg.AsyncCursor if server_side_binding else psycopg.AsyncClientCursor,
    )
    for key, param in (
        ("USER", "user"),
        ("PASSWORD", "password"),
        ("HOST", "host"),
        ("PORT", "port"),
    ):
        if settings_dict[key]:
            conn_params[param] = settings_dict[key]

    timezone, timezone_name = resolve_timezone(settings_dict, use_tz=use_tz, time_zone=time_zone)
    conn_params["context"] = adapters_template(use_tz, timezone)
    # Disable prepared statements by default to keep PgBouncer working (as Django does).
    conn_params["prepare_threshold"] = conn_params.pop("prepare_threshold", None)
    conn_params["autocommit"] = True

    # Django applies an isolation level only when OPTIONS provides one; otherwise the server (or
    # role) default stays in effect.
    isolation_level: psycopg.IsolationLevel | None = None
    if isolation_level_value is not None:
        try:
            isolation_level = psycopg.IsolationLevel(isolation_level_value)
        except ValueError:
            raise ImproperlyConfigured(
                f"Invalid transaction isolation level {isolation_level_value} specified. Use "
                "one of the psycopg.IsolationLevel values."
            ) from None

    pool_kwargs = dict(DEFAULT_POOL_OPTIONS)
    if isinstance(pool_option, dict):
        pool_kwargs.update(pool_option)
    # psycopg_pool: max_size None means "min_size"; never let a custom min_size exceed the default.
    if pool_kwargs.get("max_size") is not None:
        pool_kwargs["max_size"] = max(pool_kwargs["max_size"], pool_kwargs.get("min_size", 0))
    if settings_dict.get("CONN_HEALTH_CHECKS"):
        pool_kwargs.setdefault("check", AsyncConnectionPool.check_connection)
    return PostgresConfig(
        alias=alias,
        conn_kwargs=conn_params,
        pool_kwargs=pool_kwargs,
        isolation_level=isolation_level,
        timezone_name=timezone_name,
        assume_role=assume_role,
        server_side_binding=server_side_binding,
        server_side_cursors=not settings_dict.get("DISABLE_SERVER_SIDE_CURSORS", False),
    )
