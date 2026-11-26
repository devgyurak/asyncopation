"""Compare the ported MySQL wrapper with Django's real ``DatabaseWrapper`` (needs mysqlclient).

``django.db.backends.mysql.base`` is imported lazily, inside functions, so that importing this
module never needs MySQLdb; the drift tests skip without it, unless ``ASYNCOPATION_REQUIRE_MYSQLDB``
is set (CI), where a missing MySQLdb is a failure (``requirement_violation``).
"""

from __future__ import annotations

import contextlib
import importlib.util
import inspect
import os
from collections.abc import Callable, Generator, Mapping
from typing import Any
from unittest import mock

import pytest
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.mysql.features import DatabaseFeatures
from django.db.models.constants import OnConflict

from asyncopation.compile.mysql import MySQLCompileWrapper
from asyncopation.compile.server_info import MySQLServerInfo
from tests.support.connection_drift import cached_property_classes
from tests.support.mysql_wrappers import MYSQL_SETTINGS, fresh_wrapper
from tools.guards.archlint import Ledger

BASE_MODULE = "django.db.backends.mysql.base"
REQUIRE_VAR = "ASYNCOPATION_REQUIRE_MYSQLDB"
# The `DatabaseWrapper.<name>` rows of the ledger for django.db.backends.mysql.base (ADR-0008).
EXPECTED_PORTED: frozenset[str] = frozenset(
    {
        "vendor",
        "_data_types",
        "data_types",
        "_limited_data_types",
        "operators",
        "pattern_esc",
        "pattern_ops",
        "isolation_levels",
        "get_database_version",
        "display_name",
        "data_type_check_constraints",
        "mysql_server_data",
        "mysql_server_info",
        "mysql_version",
        "mysql_is_mariadb",
        "sql_mode",
    }
)
# Ported by the asyncmy driver (`drivers/mysql/settings.py`), not composed into the compile wrapper.
DRIVER_PORTED = frozenset({"get_connection_params", "init_connection_state"})
type Outcome = tuple[Any, ...]


def real_class() -> type[Any]:
    from django.db.backends.mysql.base import DatabaseWrapper

    return DatabaseWrapper


def ported_names() -> list[str]:
    """The ledger's ported ``DatabaseWrapper.<attr>`` names of mysql.base (prefix stripped)."""
    prefix = "DatabaseWrapper."
    return sorted(
        name.removeprefix(prefix)
        for module, name in Ledger.load().ported
        if module == BASE_MODULE
        and name.startswith(prefix)
        and name.removeprefix(prefix) not in DRIVER_PORTED
    )


def real_wrapper(info: MySQLServerInfo) -> Any:
    """Django's wrapper, constructed without I/O, with the server data injected like ours."""
    wrapper = real_class()(dict(MYSQL_SETTINGS), "real")
    wrapper.__dict__["mysql_server_data"] = {
        "version": info.version,
        "sql_mode": info.sql_mode,
        "default_storage_engine": info.default_storage_engine,
        "sql_auto_is_null": info.sql_auto_is_null,
        "lower_case_table_names": info.lower_case_table_names,
        "has_zoneinfo_database": info.has_zoneinfo_database,
    }
    return wrapper


def _refuse(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("no connection while comparing wrappers")


@contextlib.contextmanager
def connecting_forbidden() -> Generator[None]:
    """No connect path of Django's real wrapper reaches a server while active."""
    with contextlib.ExitStack() as stack:
        for name in ("get_new_connection", "ensure_connection", "create_cursor", "connect"):
            stack.enter_context(mock.patch.object(real_class(), name, _refuse))
            stack.enter_context(mock.patch.object(BaseDatabaseWrapper, name, _refuse))
        yield


def normalize(value: Any) -> Any:
    """Make values comparable across two wrapper instances (bound methods, lazy objects)."""
    return ("callable", value.__qualname__) if callable(value) else value


def observe(read: Callable[[], Any]) -> Outcome:
    try:
        return ("value", normalize(read()))
    except Exception as exc:
        return ("raises", type(exc).__name__)


def kind_of(cls: type, name: str) -> str:
    """The class of the descriptor `name` in the class body of `cls` (``cached_property``, ...)."""
    if name not in vars(cls):
        return "absent"
    descriptor = vars(cls)[name]
    # Django's `cached_property` is `django.utils.functional`'s (functools' on Django main).
    if isinstance(descriptor, cached_property_classes()):
        return "cached_property"
    return type(descriptor).__name__


def attribute_diffs(real: Any, ours: Any, names: list[str]) -> dict[str, tuple[Outcome, Outcome]]:
    diffs: dict[str, tuple[Outcome, Outcome]] = {}
    for name in names:
        pair = (_read(real, name), _read(ours, name))
        if pair[0] != pair[1]:
            diffs[name] = pair
    return diffs


def _read(wrapper: Any, name: str) -> Outcome:
    def read() -> Any:
        value = getattr(wrapper, name)
        return value() if name == "get_database_version" else value

    return observe(read)


def kind_diffs(names: list[str]) -> dict[str, tuple[str, str]]:
    """Attributes whose descriptor kind differs between Django's class and ours."""
    real = real_class()
    return {
        name: (kind_of(real, name), kind_of(MySQLCompileWrapper, name))
        for name in names
        if kind_of(real, name) != kind_of(MySQLCompileWrapper, name)
    }


def feature_names() -> list[str]:
    """Every non-dunder data attribute of Django's MySQL features: the plain class attributes
    and the cached properties / properties (methods are not features)."""
    return sorted(
        name
        for name in dir(DatabaseFeatures)
        if not name.startswith("__")
        and not inspect.isfunction(getattr(DatabaseFeatures, name, None))
    )


def feature_diffs(info: MySQLServerInfo) -> dict[str, tuple[Outcome, Outcome]]:
    diffs: dict[str, tuple[Outcome, Outcome]] = {}
    with connecting_forbidden():
        real, ours = real_wrapper(info), fresh_wrapper(info)
        for name in feature_names():
            pair = (_feature(real, name), _feature(ours, name))
            if pair[0] != pair[1]:
                diffs[name] = pair
    return diffs


def _feature(wrapper: Any, name: str) -> Outcome:
    return observe(lambda: getattr(wrapper.features, name))


def feature_outcomes(info: MySQLServerInfo) -> dict[str, Outcome]:
    """What the compile wrapper reports for each feature name (to assert the sample is broad)."""
    with connecting_forbidden():
        ours = fresh_wrapper(info)
        return {name: _feature(ours, name) for name in feature_names()}


def readable_feature_count(info: MySQLServerInfo) -> int:
    return sum(1 for outcome in feature_outcomes(info).values() if outcome[0] == "value")


OPS_CALLS: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = [
    ("for_update_sql", (), {}),
    ("for_update_sql", (), {"nowait": True}),
    ("for_update_sql", (), {"skip_locked": True, "of": ("self",)}),
    ("for_update_sql", (), {"no_key": True}),
    ("explain_query_prefix", (), {}),
    ("explain_query_prefix", ("json",), {}),
    ("explain_query_prefix", ("text",), {}),
    ("explain_query_prefix", ("tree",), {}),
    ("explain_query_prefix", (), {"analyze": True}),
    ("explain_query_prefix", ("json",), {"analyze": True}),
    ("regex_lookup", ("regex",), {}),
    ("regex_lookup", ("iregex",), {}),
    ("lookup_cast", ("exact", "JSONField"), {}),
    ("lookup_cast", ("icontains", "JSONField"), {}),
    ("lookup_cast", ("exact", "CharField"), {}),
    ("insert_statement", (), {}),
    ("insert_statement", (), {"on_conflict": OnConflict.IGNORE}),
    ("on_conflict_suffix_sql", ([], OnConflict.UPDATE, ["a", "b"], ["id"]), {}),
    ("on_conflict_suffix_sql", ([], OnConflict.IGNORE, [], []), {}),
    ("subtract_temporals", ("DateTimeField", ("l", []), ("r", [])), {}),
    ("subtract_temporals", ("TimeField", ("l", []), ("r", [])), {}),
    ("subtract_temporals", ("DateField", ("l", []), ("r", [])), {}),
    ("combine_expression", ("^", ["a", "b"]), {}),
    ("quote_name", ("t",), {}),
    ("max_name_length", (), {}),
    ("no_limit_value", (), {}),
    ("force_no_ordering", (), {}),
    ("pk_default_value", (), {}),
]


def ops_diffs(info: MySQLServerInfo) -> dict[str, tuple[Outcome, Outcome]]:
    with connecting_forbidden():
        real, ours = real_wrapper(info), fresh_wrapper(info)
        diffs: dict[str, tuple[Outcome, Outcome]] = {}
        for name, args, kwargs in OPS_CALLS:
            pair = (_call(real, name, args, kwargs), _call(ours, name, args, kwargs))
            if pair[0] != pair[1]:
                diffs[f"{name}{args}{kwargs}"] = pair
        return diffs


def _call(wrapper: Any, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Outcome:
    def run() -> Any:
        member = getattr(wrapper.ops, name)
        return member(*args, **kwargs) if callable(member) else member

    return observe(run)


def version_pattern_diff() -> tuple[str, str]:
    from django.db.backends.mysql import base

    from asyncopation.compile import mysql as ours

    return (base.server_version_re.pattern, ours.server_version_re.pattern)


def descriptor_names(names: list[str]) -> list[str]:
    """`names` without `mysql_server_data`, which Django reads with a query and we inject."""
    return [name for name in names if name != "mysql_server_data"]


def declared_on_compile_wrapper(name: str) -> bool:
    """Whether the compile wrapper declares `name` (`mysql_server_data` is only annotated)."""
    return hasattr(MySQLCompileWrapper, name) or name == "mysql_server_data"


def mysqldb_available() -> bool:
    return importlib.util.find_spec("MySQLdb") is not None


SKIP_WITHOUT_MYSQLDB = pytest.mark.skipif(
    not mysqldb_available(), reason="mysqlclient (the mysql-sync group) is not installed"
)


def requirement_violation(env: Mapping[str, str], available: bool) -> str | None:
    """Why the drift suite must fail instead of skip: CI requires MySQLdb and it is missing."""
    required = env.get(REQUIRE_VAR) == "1"
    return f"{REQUIRE_VAR}=1 but MySQLdb is not importable" if required and not available else None


def current_requirement_violation() -> str | None:
    return requirement_violation(os.environ, mysqldb_available())
