"""Drift helpers: the ported PostgreSQL connection logic against upstream Django (ADR-0007).

Upstream Django is imported directly here (tests may import Django internals); the driver side
uses a literal settings dict so no database configuration is involved.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import psycopg
from django.test import override_settings

from tests.support.drivers.admin import TzCase

DRIFT_SETTINGS: dict[str, Any] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "db",
    "USER": "user",
    "PASSWORD": "secret",
    "HOST": "db.example",
    "PORT": 5432,
    "OPTIONS": {},
    "TIME_ZONE": None,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "DISABLE_SERVER_SIDE_CURSORS": False,
    "TEST": {},
}
POPPED_OPTIONS = ("assume_role", "isolation_level", "pool", "server_side_binding")


def drift_settings(
    name: str | None = "db", options: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    settings_dict = copy.deepcopy(DRIFT_SETTINGS)
    settings_dict["NAME"] = name
    settings_dict["OPTIONS"] = copy.deepcopy(options or {})
    settings_dict.update(extra)
    return settings_dict


def upstream_wrapper(settings_dict: dict[str, Any], alias: str = "default") -> Any:
    from django.db.backends.postgresql.base import DatabaseWrapper

    return DatabaseWrapper(copy.deepcopy(settings_dict), alias)


def offline_driver(options: dict[str, Any] | None = None) -> Any:
    """A `PostgresDriver` built from the literal settings (no I/O, never opened)."""
    from asyncopation.drivers.postgresql import PostgresDriver

    return PostgresDriver.from_settings(
        "default", drift_settings("db", options), use_tz=True, time_zone="UTC"
    )


def build(settings_dict: dict[str, Any], case: TzCase | None = None, alias: str = "default") -> Any:
    from asyncopation.drivers.postgresql.settings import build_config

    use_tz = True if case is None else case.use_tz
    zone = "UTC" if case is None else case.settings_time_zone
    return build_config(alias, settings_dict, use_tz=use_tz, time_zone=zone)


# --------------------------------------------------------------------------- error classes
class _StubWrapper:
    """The two attributes `DatabaseErrorWrapper` touches on a connection wrapper."""

    def __init__(self, dbapi: ModuleType) -> None:
        self.Database = dbapi
        self.errors_occurred = False


def psycopg_error_classes() -> list[type[psycopg.Error]]:
    """Every exception class of psycopg's hierarchy (top-level and `psycopg.errors`)."""
    import psycopg.errors

    found: dict[str, type[psycopg.Error]] = {}
    for module in (psycopg, psycopg.errors):
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, psycopg.Error):
                found[name] = obj
    return [found[name] for name in sorted(found)]


def django_wrapper_class(exc: Exception, dbapi: ModuleType = psycopg) -> type[BaseException]:
    """The exception class Django's own `DatabaseErrorWrapper` raises for `exc`."""
    from django.db.utils import DatabaseErrorWrapper

    try:
        with DatabaseErrorWrapper(_StubWrapper(dbapi)):
            raise exc
    except Exception as raised:
        return type(raised)
    raise AssertionError("DatabaseErrorWrapper must raise")


def our_mapped_class(exc: Exception, dbapi: ModuleType = psycopg) -> type[BaseException] | None:
    from asyncopation.drivers.errors import map_exception

    mapped = map_exception(exc, dbapi)
    return None if mapped is None else type(mapped)


def mapped_from(exc: Exception, dbapi: ModuleType = psycopg) -> Exception:
    """The `django.db` exception for `exc` the way the driver raises it (with `__cause__`)."""
    from asyncopation.drivers.errors import raise_mapped

    try:
        raise_mapped(exc, dbapi)
    except Exception as mapped:
        return mapped
    raise AssertionError("raise_mapped must raise")


def instance_of(cls: type[Exception], *args: Any) -> Exception:
    return cls(*args)


def custom_subclass(base: type[Exception]) -> type[Exception]:
    return type("CustomDriverError", (base,), {})


class _StubInfo:
    """psycopg's `ErrorInfo` protocol: `error_field(code)` returns bytes or None."""

    def __init__(self, fields: dict[int, bytes]) -> None:
        self._fields = fields

    def error_field(self, field: int) -> bytes | None:
        return self._fields.get(field)


def error_with_diag(cls: type[psycopg.Error], *, severity: bytes, sqlstate: bytes | None) -> Any:
    """A psycopg error whose `diag` reports `severity` (non-localized) and optional SQLSTATE."""
    fields = {psycopg.pq.DiagnosticField.SEVERITY_NONLOCALIZED: severity}
    if sqlstate is not None:
        fields[psycopg.pq.DiagnosticField.SQLSTATE] = sqlstate
    return cls("x", info=_StubInfo(fields))  # ty: ignore[invalid-argument-type]


def lookup_error(sqlstate: str) -> Any:
    return psycopg.errors.lookup(sqlstate)("x")


@dataclass(frozen=True)
class ErrorPairs:
    first: Any
    second: Any

    @property
    def same_object(self) -> bool:
        return self.first is self.second

    @property
    def django_names(self) -> list[str]:
        return [django.__name__ for django, _ in self.first]

    @property
    def dbapi_names(self) -> list[str]:
        return [dbapi.__name__ for _, dbapi in self.first]


def error_pairs_twice(dbapi: ModuleType = psycopg) -> ErrorPairs:
    from asyncopation.drivers.errors import django_error_pairs

    return ErrorPairs(django_error_pairs(dbapi), django_error_pairs(dbapi))


# --------------------------------------------------------------------------- connection params
@dataclass(frozen=True)
class ConnKwargsComparison:
    ours: dict[str, Any]
    reference: dict[str, Any]

    def differences(self) -> dict[str, tuple[Any, Any]]:
        """Keys whose values differ.

        The cursor factory is compared by name; `autocommit` is ours alone (Django sets it in
        `get_new_connection`, we pass it to the pool's connections).
        """
        keys = (set(self.ours) | set(self.reference)) - {"cursor_factory", "autocommit"}
        return {
            k: (self.ours.get(k), self.reference.get(k))
            for k in sorted(keys)
            if self.ours.get(k) != self.reference.get(k)
        }

    def cursor_factories_correspond(self) -> bool:
        """Django's factory is `CursorMixin` + the sync psycopg class we mirror asynchronously."""
        ours = self.ours["cursor_factory"].__name__.removeprefix("Async")
        return ours in [c.__name__ for c in self.reference["cursor_factory"].__mro__]


def compare_conn_kwargs(
    name: str | None, options: dict[str, Any] | None, case: TzCase | None = None
) -> ConnKwargsComparison:
    settings_dict = drift_settings(name, options)
    ours = build(settings_dict, case).conn_kwargs
    use_tz = True if case is None else case.use_tz
    zone = "UTC" if case is None else case.settings_time_zone
    with override_settings(USE_TZ=use_tz, TIME_ZONE=zone):
        reference = upstream_wrapper(settings_dict).get_connection_params()
    return ConnKwargsComparison(ours, reference)


def leftover_option_keys(options: dict[str, Any]) -> list[str]:
    kwargs = build(drift_settings("db", options)).conn_kwargs
    return [k for k in POPPED_OPTIONS if k in kwargs]


def timezone_pair(case: TzCase) -> tuple[Any, Any]:
    """(our `(tzinfo, name)`, Django's `(wrapper.timezone, wrapper.timezone_name)`)."""
    from asyncopation.drivers.postgresql.settings import resolve_timezone

    settings_dict = drift_settings("db", None, TIME_ZONE=case.db_time_zone)
    ours = resolve_timezone(settings_dict, use_tz=case.use_tz, time_zone=case.settings_time_zone)
    with override_settings(USE_TZ=case.use_tz, TIME_ZONE=case.settings_time_zone):
        wrapper = upstream_wrapper(settings_dict)
        return ours, (wrapper.timezone, wrapper.timezone_name)


def quote_pair(name: str) -> tuple[str, str]:
    from django.db.backends.postgresql.operations import DatabaseOperations

    from asyncopation.drivers.postgresql.connection import _quote

    return _quote(name), DatabaseOperations(None).quote_name(name)


def cursor_base_names() -> tuple[list[str], list[str]]:
    """(our stream cursor bases, Django's `ServerSideCursor` bases) without `Async`/`CursorMixin`."""
    import django.db.backends.postgresql.base as upstream

    from asyncopation.drivers.postgresql.connection import AsyncClientServerCursor

    ours = [c.__name__.removeprefix("Async") for c in AsyncClientServerCursor.__bases__]
    server_side_cursor = vars(upstream)["ServerSideCursor"]  # defined under `if is_psycopg3`
    theirs = [c.__name__ for c in server_side_cursor.__bases__ if c.__name__ != "CursorMixin"]
    return ours, theirs


# --------------------------------------------------------------------------- config errors
def noop_hook(*args: Any) -> None:
    return None


def config_error(
    options: dict[str, Any] | None = None, *, name: str | None = "db", **extra: Any
) -> BaseException | None:
    """The exception `build_config` raises for these settings (None when it accepts them)."""
    try:
        build(drift_settings(name, options, **extra))
    except Exception as exc:
        return exc
    return None


def pool_kwargs_for(settings_overrides: dict[str, Any], pool: Any = None) -> dict[str, Any]:
    options = {"pool": pool} if pool is not None else None
    return build(drift_settings("db", options, **settings_overrides)).pool_kwargs


def pool_true_matches_defaults() -> bool:
    return pool_kwargs_for({}, True) == pool_kwargs_for({}, None)


def timezone_name_for(case: TzCase) -> str:
    settings_dict = drift_settings("db", None, TIME_ZONE=case.db_time_zone)
    return build(settings_dict, case).timezone_name


def signature(exc: BaseException | None) -> tuple[str, str] | None:
    return None if exc is None else (type(exc).__name__, str(exc))


def upstream_error(call: str, settings_dict: dict[str, Any], *, use_tz: bool = True) -> Any:
    """`signature` of the error an upstream wrapper method raises (`call` is a method name)."""
    with override_settings(USE_TZ=use_tz, TIME_ZONE="UTC" if use_tz else "Asia/Seoul"):
        wrapper = upstream_wrapper(settings_dict)
        try:
            getattr(wrapper, call)(*({},) if call == "get_new_connection" else ())
        except Exception as exc:
            return signature(exc)
    return None


def our_error(settings_dict: dict[str, Any], *, use_tz: bool = True) -> Any:
    zone = "UTC" if use_tz else "Asia/Seoul"
    try:
        build(settings_dict, TzCase(use_tz, settings_dict["TIME_ZONE"], zone))
    except Exception as exc:
        return signature(exc)
    return None
