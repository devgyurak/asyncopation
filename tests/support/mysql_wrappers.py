"""MySQL / MariaDB compile-only wrapper builders and server scenarios (ADR-0008 Decision 5)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

import pytest

from asyncopation.compile.mysql import MySQLCompileWrapper, wrapper_for
from asyncopation.compile.server_info import MySQLServerInfo

MYSQL_SETTINGS: dict[str, Any] = {
    "ENGINE": "django.db.backends.mysql",
    "NAME": "x",
    "OPTIONS": {},
    "TIME_ZONE": None,
    "CONN_MAX_AGE": 0,
    "CONN_HEALTH_CHECKS": False,
    "AUTOCOMMIT": True,
    "ATOMIC_REQUESTS": False,
    "USER": "",
    "PASSWORD": "",
    "HOST": "",
    "PORT": "",
    "TEST": {},
}
MODE = "STRICT_TRANS_TABLES,NO_ZERO_DATE"

# Release strings as `SELECT VERSION()` reports them.
VERSIONS: dict[str, str] = {
    "mysql-8.4": "8.4.3",
    "mysql-8.0": "8.0.36",
    "mysql-5.7": "5.7.44",  # below Django 6.1's minimum: nothing in `features` gates on it
    "mariadb-11.8": "11.8.1-MariaDB-ubu2404",
    "mariadb-10.11": "10.11.6-MariaDB",
    "mariadb-11.7": "11.7.0-MariaDB",
    "mariadb-11.6": "11.6.9-MariaDB",
}


def mysql_info(
    version: str = "8.4.3", sql_mode: str = MODE, engine: str = "InnoDB"
) -> MySQLServerInfo:
    return MySQLServerInfo(
        version=version,
        sql_mode=sql_mode,
        default_storage_engine=engine,
        sql_auto_is_null=False,
        lower_case_table_names=False,
        has_zoneinfo_database=True,
    )


def scenario_info(scenario: str) -> MySQLServerInfo:
    return mysql_info(VERSIONS[scenario])


def make_mysql_wrapper(alias: str, version: str = "8.4.3") -> MySQLCompileWrapper:
    """The shared compile-only MySQL wrapper of `alias`."""
    return wrapper_for(alias, dict(MYSQL_SETTINGS), mysql_info(version))


def fresh_wrapper(info: MySQLServerInfo, alias: str = "fresh") -> MySQLCompileWrapper:
    """An unregistered wrapper with `info` injected."""
    wrapper = MySQLCompileWrapper(dict(MYSQL_SETTINGS), alias)
    wrapper.apply_server_info(info)
    return wrapper


def resolve(wrapper: Any, path: str) -> Any:
    """`wrapper.features.minimum_database_version`-style lookup of a dotted path."""
    value = wrapper
    for part in path.split("."):
        value = getattr(value, part.removesuffix("()"))
        value = value() if part.endswith("()") else value
    return value


def ops_call(wrapper: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(wrapper.ops, name)(*args, **kwargs)


@dataclass(frozen=True)
class Facts:
    """What a server release must make a freshly built wrapper report, before any connection."""

    min_version: tuple[int, int]
    version: tuple[int, int, int]
    mariadb: bool
    uuid_type: str
    explain_prefix: str
    regex: str
    uuid4_function: bool
    select_for_update_of: bool


FACTS: dict[str, Facts] = {
    "mysql-8.4": Facts((8, 4), (8, 4, 3), False, "char(32)", "EXPLAIN FORMAT=TREE", "c", False, True),
    "mysql-5.7": Facts((8, 4), (5, 7, 44), False, "char(32)", "EXPLAIN FORMAT=TREE", "c", False, True),
    "mysql-8.0": Facts((8, 4), (8, 0, 36), False, "char(32)", "EXPLAIN FORMAT=TREE", "c", False, True),
    "mariadb-11.8": Facts((10, 11), (11, 8, 1), True, "uuid", "EXPLAIN", "b", True, False),
    "mariadb-10.11": Facts((10, 11), (10, 11, 6), True, "uuid", "EXPLAIN", "b", False, False),
    "mariadb-11.7": Facts((10, 11), (11, 7, 0), True, "uuid", "EXPLAIN", "b", True, False),
    "mariadb-11.6": Facts((10, 11), (11, 6, 9), True, "uuid", "EXPLAIN", "b", False, False),
}  # fmt: skip

assert set(FACTS) == set(VERSIONS), "every server scenario needs its expected facts"

_REGEX = {"c": "REGEXP_LIKE(%s, %s, 'c')", "b": "%s REGEXP BINARY %s"}


def fact_params() -> list[Any]:
    """(scenario, dotted path, expected) for every injected fact."""
    out: list[Any] = []
    for scenario, f in FACTS.items():
        rows = {
            "features.minimum_database_version": f.min_version,
            "mysql_version": f.version,
            "get_database_version()": f.version,
            "mysql_is_mariadb": f.mariadb,
            "display_name": "MariaDB" if f.mariadb else "MySQL",
            "data_types": None,
            "features.supports_uuid4_function": f.uuid4_function,
            "features.has_select_for_update_of": f.select_for_update_of,
            "features.has_native_uuid_field": f.mariadb,
            "features.update_can_self_select": f.mariadb,
            "features.supports_any_value": not f.mariadb,
        }
        for path, expected in rows.items():
            if path == "data_types":
                path, expected = "data_types.UUIDField", f.uuid_type
            out.append(pytest.param(scenario, path, expected, id=f"{scenario}-{path}"))
    return out


def fact_of(wrapper: Any, path: str) -> Any:
    """`resolve`, indexing a dict when the last part names a key of it (`data_types.UUIDField`)."""
    head, _, tail = path.partition(".")
    if head == "data_types":
        return wrapper.data_types[tail]
    return resolve(wrapper, path)


def feature_before_and_after(feature: str, change: dict[str, Any]) -> tuple[Any, Any]:
    """`feature` of a wrapper for the default server and of one whose server facts have `change`."""
    before = fact_of(fresh_wrapper(mysql_info()), f"features.{feature}")
    after = fact_of(fresh_wrapper(mutate_info(mysql_info(), **change)), f"features.{feature}")
    return before, after


def explain_prefix_of(wrapper: Any) -> str:
    return ops_call(wrapper, "explain_query_prefix")


def regex_of(wrapper: Any) -> str:
    return ops_call(wrapper, "regex_lookup", "regex")


def expected_explain(scenario: str) -> str:
    return FACTS[scenario].explain_prefix


def expected_regex(scenario: str) -> str:
    return _REGEX[FACTS[scenario].regex]


def check_constraint_names(wrapper: Any) -> list[str]:
    return sorted(wrapper.data_type_check_constraints)


def mutate_info(info: MySQLServerInfo, **changes: Any) -> MySQLServerInfo:
    return replace(info, **changes)


HANDSHAKE_VERSION = "5.5.5-10.11.6-MariaDB"
"""The server version a MariaDB handshake reports (replication hack), not what ``VERSION()`` returns."""


def integer_flags_info() -> MySQLServerInfo:
    """Server info whose three flags are the integers ``@@sql_auto_is_null`` etc. return."""
    return replace(
        mysql_info(),
        sql_auto_is_null=cast(Any, 1),
        lower_case_table_names=cast(Any, 2),
        has_zoneinfo_database=cast(Any, 0),
    )


def injected_flags(info: MySQLServerInfo) -> tuple[Any, ...]:
    """The three flags of `mysql_server_data` after injection (`value`, `type` pairs)."""
    data = fresh_wrapper(info).mysql_server_data
    keys = ("sql_auto_is_null", "lower_case_table_names", "has_zoneinfo_database")
    return tuple((data[key], type(data[key])) for key in keys)


def version_facts(version: str) -> tuple[Any, ...]:
    """``(mysql_version, mysql_is_mariadb)`` of a wrapper built for `version`."""
    wrapper = fresh_wrapper(mysql_info(version))
    return (wrapper.mysql_version, wrapper.mysql_is_mariadb)
