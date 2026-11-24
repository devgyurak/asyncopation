"""Translate a Django ``DATABASES`` entry into psycopg connection settings.

Ported from ``django/db/backends/postgresql/base.py`` (``DatabaseWrapper.get_connection_params``,
``_configure_connection`` inputs) and ``django/db/backends/base/base.py`` (``timezone`` /
``timezone_name``) @ 6.1.3. Pure functions: no I/O, no connection.
"""

from __future__ import annotations

import datetime
import inspect
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from django.core.exceptions import ImproperlyConfigured
from psycopg_pool import AsyncConnectionPool

from asyncopation._django.postgresql import adapters_template

# psycopg_pool defaults are min_size=4 / max_size=min_size; these are provisional until the
# ADR-0004 implementation issue benchmarks pool sizing.
DEFAULT_POOL_OPTIONS: dict[str, Any] = {"min_size": 1, "max_size": 10}

# OPTIONS["pool"] keys rejected by NAME because the driver passes the same argument itself
# (psycopg_pool would raise "multiple values for keyword argument"): `name` (the alias),
# `configure` (the per-connection session setup), `kwargs` / `conninfo` (the connection settings)
# and `open` (the pool is opened by `PostgresDriver.open()`). Every other key is validated by
# VALUE: callables are sync hooks (or classes) that do not fit an async pool, and
# `close_returns=True` would make a discarded connection go back to the pool.
_DRIVER_OWNED_POOL_KEYS = frozenset({"name", "configure", "kwargs", "conninfo", "open"})
_POOL_PARAMETERS = frozenset(inspect.signature(AsyncConnectionPool.__init__).parameters) - {"self"}

# Ported from django/db/backends/postgresql/operations.py::max_name_length @ 6.1.3 - unchanged.
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
    """Resolve a ``DATABASES`` entry (see the module docstring); raises ``ImproperlyConfigured``."""
    # Ported from django/db/backends/postgresql/base.py::get_connection_params and
    # django/db/backends/base/base.py::check_settings @ 6.1.3
    # Changes: OPTIONS is copied, not mutated; cursor classes are the async psycopg ones; the pool
    # reuses OPTIONS["pool"] for psycopg_pool's AsyncConnectionPool (validated below); autocommit
    # is always on (the driver leaves it only inside begin()); the isolation level is parsed here
    # instead of in get_new_connection; Django's CONN_MAX_AGE-vs-pool check is omitted (the
    # driver pools connections itself and never closes them on request end).
    if settings_dict.get("TIME_ZONE") is not None and not use_tz:
        raise ImproperlyConfigured(
            f"Connection '{alias}' cannot set TIME_ZONE because USE_TZ is False."
        )
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
    if "cursor_factory" in options:
        raise ImproperlyConfigured(
            "OPTIONS['cursor_factory'] is a sync psycopg cursor class; the async driver chooses "
            "its cursors itself (use OPTIONS['server_side_binding'] to switch binding)."
        )
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

    pool_kwargs = _pool_kwargs(pool_option)
    if settings_dict.get("CONN_HEALTH_CHECKS") and "check" not in pool_kwargs:
        pool_kwargs["check"] = AsyncConnectionPool.check_connection
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


def _pool_kwargs(option: Any) -> dict[str, Any]:
    """``OPTIONS["pool"]`` -> ``AsyncConnectionPool`` keyword arguments (see the key rules above).

    ``None`` / ``True`` / ``False`` mean the defaults (the async side always pools); a mapping
    overrides them. Sizes are not corrected: ``min_size > max_size`` is psycopg_pool's ``ValueError``
    when the driver opens.
    """
    if option is None or isinstance(option, bool):
        return dict(DEFAULT_POOL_OPTIONS)
    if not isinstance(option, Mapping):
        raise ImproperlyConfigured(
            f"OPTIONS['pool'] must be a mapping, True, False or None, not {type(option).__name__}."
        )
    keys = {str(key) for key in option}
    unknown = sorted(keys - _POOL_PARAMETERS)
    owned = sorted(keys & _DRIVER_OWNED_POOL_KEYS)
    by_value = sorted(
        str(key)
        for key, value in option.items()
        if callable(value) or (key == "close_returns" and value is True)
    )
    if unknown or owned or by_value:
        problems = {
            "unknown keys": unknown,
            "keys the driver sets itself": owned,
            "callables (hooks are managed by the driver) or close_returns=True": by_value,
        }
        raise ImproperlyConfigured(
            "OPTIONS['pool'] cannot be used with the async pool: "
            + "; ".join(f"{name} {names}" for name, names in problems.items() if names)
        )
    for size in ("min_size", "max_size"):
        if size not in option or (size == "max_size" and option[size] is None):
            continue  # max_size=None means "min_size" to psycopg_pool
        if isinstance(option[size], bool) or not isinstance(option[size], int):
            raise ImproperlyConfigured(
                f"OPTIONS['pool'][{size!r}] must be an integer, not {option[size]!r}."
            )
    kwargs = dict(option)
    # Defaults only fill what the user left out; a size the user gave is never corrected.
    kwargs.setdefault("min_size", DEFAULT_POOL_OPTIONS["min_size"])
    kwargs.setdefault("max_size", max(DEFAULT_POOL_OPTIONS["max_size"], kwargs["min_size"] or 0))
    return kwargs
