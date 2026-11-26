"""Translate a Django ``DATABASES`` entry into asyncmy connection and pool settings.

Ported from ``django/db/backends/mysql/base.py`` (``DatabaseWrapper.get_connection_params``,
``django_conversions``, ``init_connection_state`` inputs) and ``django/db/backends/base/base.py``
(``check_settings``) @ 6.1.3. Pure functions: no I/O, no connection.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import asyncmy
import asyncmy.converters
from asyncmy.constants import CLIENT, FIELD_TYPE
from django.core.exceptions import ImproperlyConfigured

from asyncopation._django.mysql import ISOLATION_LEVELS, typecast_time

# Provisional pool sizes until the #8 benchmark (ADR-0001 Decision 8); the acquire timeout is the
# psycopg_pool default.
DEFAULT_POOL_OPTIONS: dict[str, Any] = {
    "min_size": 1,
    "max_size": 10,
    "timeout": 30.0,
    "max_waiting": 0,  # 0: no limit on acquires in progress
    "max_idle": 3600,  # asyncmy `pool_recycle`: a connection idle this long is replaced (seconds)
}
_POOL_KEYS = frozenset(DEFAULT_POOL_OPTIONS)
_CONNECT_PARAMETERS = frozenset(inspect.signature(asyncmy.connect).parameters)
# OPTIONS keys the driver owns. `conv` carries the decoder shim (a replacement would silently
# change returned types), `autocommit` is always on (ADR-0003), `cursor_cls` and `echo` belong to
# the driver, `query_callback` would run sync code in the event loop.
_DRIVER_OWNED_OPTIONS = frozenset({"conv", "autocommit", "cursor_cls", "echo", "query_callback"})

_ZERO_DATE_TYPES = (FIELD_TYPE.DATE, FIELD_TYPE.DATETIME, FIELD_TYPE.TIMESTAMP)


def _none_when_invalid(decode: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """mysqlclient returns ``None`` for a date it cannot represent (``'0000-00-00'``); asyncmy
    returns the raw string."""

    def decoder(value: Any) -> Any:
        decoded = decode(value)
        return None if isinstance(decoded, str) else decoded

    return decoder


class SqlLiteral:
    """A parameter the driver rendered itself (see ``encode_literal``)."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def encode_literal(value: SqlLiteral, mapping: Any = None) -> str:
    """The literal text of a ``SqlLiteral``.

    asyncmy escapes ``bytes`` and strings inside a sequence with backslash rules whatever the
    server's ``sql_mode`` is, so with ``NO_BACKSLASH_ESCAPES`` (settable through
    ``OPTIONS["init_command"]`` or ``OPTIONS["sql_mode"]``) a quote in a value would end the string
    literal early (SQL injection). The connection renders those values itself (binary values as a
    hex literal, a binary string in both modes) and hands the text through this encoder.
    """
    return value.text


def build_conversions() -> dict[Any, Any]:
    """The decoder shim: mysqlclient's ``conversions`` as Django uses them (ADR-0003 Decision 1).

    asyncmy's ``conv`` replaces its whole table, so this starts from ``asyncmy.converters``:
    ``TIME`` is ``typecast_time`` (Django's ``django_conversions``) and the date types return
    ``None`` for zero dates like mysqlclient. Every other decoder and every encoder is asyncmy's.
    """
    conversions = dict(asyncmy.converters.conversions)
    conversions[SqlLiteral] = encode_literal
    conversions[FIELD_TYPE.TIME] = typecast_time
    for field_type in _ZERO_DATE_TYPES:
        conversions[field_type] = _none_when_invalid(conversions[field_type])
    return conversions


@dataclass(frozen=True)
class MySQLConfig:
    """Everything a ``MySQLDriver`` needs, resolved without I/O."""

    alias: str
    conn_kwargs: dict[str, Any]
    """``asyncmy.connect`` arguments, with ``init_command`` = the user's command plus the
    isolation level assignment (what the probe connection runs)."""
    pool_kwargs: dict[str, Any]
    """``asyncmy.create_pool`` sizing arguments (``minsize``, ``maxsize``, ``pool_recycle``)."""
    acquire_timeout: float
    max_waiting: int
    isolation_level: str | None  # None: keep the server default
    server_side_cursors: bool
    health_checks: bool
    user_init_command: str = ""

    def pooled_kwargs(self, *, sql_auto_is_null: bool) -> dict[str, Any]:
        """Connection arguments of a pool member.

        Django's ``init_connection_state`` runs ``SET SQL_AUTO_IS_NULL = 0`` only when the server
        has it on (``mysql_server_data``), so the pool's ``init_command`` is composed after the
        probe: the user's command, then the assignments.
        """
        assignments = []
        if sql_auto_is_null:
            assignments.append("SET SQL_AUTO_IS_NULL = 0")
        if self.isolation_level:
            assignments.append(
                f"SET SESSION TRANSACTION ISOLATION LEVEL {self.isolation_level.upper()}"
            )
        return {
            **self.conn_kwargs,
            "init_command": compose_init_command(self.user_init_command, assignments),
        }


def compose_init_command(user_command: str | None, assignments: list[str]) -> str | None:
    """``user_command; assignments...`` as one ``init_command`` (asyncmy runs it as a single
    multi-statement query). The user's trailing ``;`` and whitespace are stripped so the joined
    text has no ``;;``."""
    head = (user_command or "").strip().rstrip(";").rstrip()
    tail = "; ".join(assignments)
    # A newline before the `;` ends a trailing `-- ...` or `# ...` comment of the user's command,
    # which would otherwise swallow the driver's assignments.
    return (f"{head}\n; {tail}" if head and tail else head or tail) or None


def build_config(
    alias: str,
    settings_dict: dict[str, Any],
    *,
    use_tz: bool,
    time_zone: str,
) -> MySQLConfig:
    """Resolve a ``DATABASES`` entry (see the module docstring); raises ``ImproperlyConfigured``."""
    # Ported from django/db/backends/mysql/base.py::get_connection_params and
    # init_connection_state @ 6.1.3
    # Ported from django/db/backends/base/base.py::check_settings @ 6.1.3
    # Changes: OPTIONS is copied and validated against asyncmy.connect's parameters (mysqlclient
    # only keys raise); OPTIONS["sql_mode"] is quoted and run after the user's init_command;
    # autocommit is always on; the isolation level is parsed here instead of in
    # get_connection_params' side effect; the pool is configured in
    # settings_dict["ASYNCOPATION"]["pool"], never in OPTIONS, because Django's own MySQL backend
    # forwards every OPTIONS key to MySQLdb.connect (ADR-0001 Decision 8).
    if settings_dict.get("TIME_ZONE") is not None and not use_tz:
        raise ImproperlyConfigured(
            f"Connection '{alias}' cannot set TIME_ZONE because USE_TZ is False."
        )
    kwargs: dict[str, Any] = {"conv": build_conversions(), "charset": "utf8mb4"}
    if settings_dict["USER"]:
        kwargs["user"] = settings_dict["USER"]
    if settings_dict["NAME"]:
        kwargs["database"] = settings_dict["NAME"]
    if settings_dict["PASSWORD"]:
        kwargs["password"] = settings_dict["PASSWORD"]
    if settings_dict["HOST"].startswith("/"):
        kwargs["unix_socket"] = settings_dict["HOST"]
    elif settings_dict["HOST"]:
        kwargs["host"] = settings_dict["HOST"]
    if settings_dict["PORT"]:
        kwargs["port"] = int(settings_dict["PORT"])
    # We need the number of potentially affected rows after an "UPDATE", not the number of
    # changed rows.
    kwargs["client_flag"] = CLIENT.FOUND_ROWS
    options = dict(settings_dict.get("OPTIONS") or {})
    isolation_level = options.pop("isolation_level", "read committed")
    if isolation_level:
        isolation_level = isolation_level.lower()
        if isolation_level not in ISOLATION_LEVELS:
            raise ImproperlyConfigured(
                f"Invalid transaction isolation level '{isolation_level}' specified.\n"
                f"Use one of {', '.join(f"'{s}'" for s in sorted(ISOLATION_LEVELS))}, or None."
            )
    _check_options(alias, options)
    user_init_command = options.pop("init_command", None) or ""
    sql_mode = options.pop("sql_mode", None)
    if sql_mode is not None:
        # asyncmy sends `SET sql_mode=<value>` unquoted, which fails for a list of modes (1064):
        # the driver quotes it and, like mysqlclient (which sets it after connecting, so after
        # the `init_command`), runs it after the user's command. An empty value is ignored.
        if not isinstance(sql_mode, str) or "'" in sql_mode or "\\" in sql_mode:
            raise ImproperlyConfigured(
                f"DATABASES[{alias!r}]['OPTIONS']['sql_mode'] must be a string without quotes "
                "or backslashes, for example 'TRADITIONAL,NO_BACKSLASH_ESCAPES'."
            )
        if sql_mode:
            user_init_command = "\n; ".join(
                part
                for part in (user_init_command.strip().rstrip(";"), f"SET sql_mode='{sql_mode}'")
                if part.strip()
            )
    kwargs.update(options)
    kwargs["autocommit"] = True
    pool_options = _pool_options(alias, settings_dict.get("ASYNCOPATION"))
    config = MySQLConfig(
        alias=alias,
        conn_kwargs=kwargs,
        pool_kwargs={
            "minsize": pool_options["min_size"],
            "maxsize": pool_options["max_size"],
            "pool_recycle": pool_options["max_idle"],
        },
        acquire_timeout=float(pool_options["timeout"]),
        max_waiting=pool_options["max_waiting"],
        isolation_level=isolation_level or None,
        server_side_cursors=not settings_dict.get("DISABLE_SERVER_SIDE_CURSORS", False),
        health_checks=bool(settings_dict.get("CONN_HEALTH_CHECKS")),
        user_init_command=user_init_command,
    )
    kwargs["init_command"] = config.pooled_kwargs(sql_auto_is_null=False)["init_command"]
    return config


def _check_options(alias: str, options: Mapping[str, Any]) -> None:
    unknown = sorted(str(key) for key in options if key not in _CONNECT_PARAMETERS)
    owned = sorted(str(key) for key in options if key in _DRIVER_OWNED_OPTIONS)
    problems = []
    if unknown:
        problems.append(f"keys asyncmy does not accept {unknown}")
    if owned:
        problems.append(f"keys the driver sets itself {owned}")
    if options.get("binary_prefix"):
        problems.append(
            "'binary_prefix' has no effect with asyncmy (it accepts the key and ignores it; "
            "mysqlclient adds the `_binary` prefix)"
        )
    if options.get("stmt_cache_size"):
        problems.append(
            "'stmt_cache_size' must stay 0 (asyncmy's prepared statements ignore the decoder shim)"
        )
    if problems:
        raise ImproperlyConfigured(
            f"DATABASES[{alias!r}]['OPTIONS'] cannot be used with asyncmy: " + "; ".join(problems)
        )


def _pool_options(alias: str, option: Any) -> dict[str, Any]:
    """``DATABASES[alias]["ASYNCOPATION"]["pool"]`` -> validated pool options with defaults."""
    where = f"DATABASES[{alias!r}]['ASYNCOPATION']"
    if option is None:
        return dict(DEFAULT_POOL_OPTIONS)
    if not isinstance(option, Mapping) or set(option) - {"pool"}:
        raise ImproperlyConfigured(f"{where} must be a mapping with the single key 'pool'.")
    pool = option.get("pool")
    if pool is None:
        return dict(DEFAULT_POOL_OPTIONS)
    if not isinstance(pool, Mapping):
        raise ImproperlyConfigured(f"{where}['pool'] must be a mapping, not {type(pool).__name__}.")
    unknown = sorted(str(key) for key in pool if key not in _POOL_KEYS)
    invalid = sorted(
        str(key) for key, value in pool.items() if key in _POOL_KEYS and _invalid(key, value)
    )
    if unknown or invalid:
        raise ImproperlyConfigured(
            f"{where}['pool'] is invalid: unknown keys {unknown}; invalid values {invalid} "
            "(min_size int >= 0, max_size int >= 1, max_waiting int >= 0, "
            "max_idle int >= 1 (whole seconds a connection may stay idle, asyncmy `pool_recycle`), "
            "timeout int or float > 0; a bool is never accepted)."
        )
    merged = {**DEFAULT_POOL_OPTIONS, **pool}
    if "max_size" not in pool:
        merged["max_size"] = max(DEFAULT_POOL_OPTIONS["max_size"], merged["min_size"])
    if merged["min_size"] > merged["max_size"]:
        raise ImproperlyConfigured(
            f"{where}['pool']: min_size {merged['min_size']} is greater than max_size "
            f"{merged['max_size']}."
        )
    return merged


_MINIMUMS = {"min_size": 0, "max_size": 1, "max_waiting": 0, "max_idle": 1}


def _invalid(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if key in _MINIMUMS:
        return not isinstance(value, int) or value < _MINIMUMS[key]
    return not isinstance(value, int | float) or value <= 0
