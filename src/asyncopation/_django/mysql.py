"""MySQL / MariaDB boundary of the Django wrapper (ADR-0008 Decision 5).

Re-exports the pieces of Django's MySQL backend that the compile-only wrapper is composed from,
and injects the server data into it. Nothing here imports ``django.db.backends.mysql.base`` or
``MySQLdb``: Django 6.1's ``mysql.features``, ``mysql.operations``, ``mysql.client`` and
``mysql.creation`` do not either (``mysql.introspection`` and ``mysql.base`` do, so the backend-neutral
base introspection stands in), so MySQL support never needs mysqlclient. Import it lazily. Only
``compile/`` may use ``BaseWrapper``.
"""

from __future__ import annotations

import re
from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper as BaseWrapper
from django.db.backends.base.introspection import BaseDatabaseIntrospection
from django.db.backends.mysql.client import DatabaseClient
from django.db.backends.mysql.creation import DatabaseCreation
from django.db.backends.mysql.features import DatabaseFeatures
from django.db.backends.mysql.operations import DatabaseOperations
from django.db.backends.utils import typecast_time

__all__ = [
    "CODES_FOR_INTEGRITYERROR",
    "ISOLATION_LEVELS",
    "MYSQL_SERVER_DATA_SQL",
    "SERVER_DATA_KEYS",
    "BaseWrapper",
    "DatabaseClient",
    "DatabaseCreation",
    "DatabaseFeatures",
    "DatabaseIntrospection",
    "DatabaseOperations",
    "inject_mysql_server_data",
    "mysql_server_data_from_row",
    "parse_server_version",
    "typecast_time",
]

DatabaseIntrospection = BaseDatabaseIntrospection
"""Stand-in for ``mysql.introspection.DatabaseIntrospection`` (which imports MySQLdb)."""


def inject_mysql_server_data(wrapper: Any, data: dict[str, Any]) -> None:
    """Pre-fill ``mysql_server_data`` (a ``cached_property`` that would otherwise connect)."""
    wrapper.__dict__["mysql_server_data"] = data


# Ported from django/db/backends/mysql/base.py (CursorWrapper.codes_for_integrityerror,
# DatabaseWrapper.isolation_levels, the mysql_server_data SELECT and dict, server_version_re and
# the mysql_version parsing) @ 6.1.3
# Changes: the constants are module level values the asyncmy driver reads (the wrapper itself is
# composed in `compile/mysql.py`); `mysql_server_data` is not a property: the driver runs the
# SELECT and the injection pre-fills it.

# This should match the numerical portion of the version numbers (we can treat versions like
# 5.0.24 and 5.0.24a as the same). (Django compiles it lazily; this module is imported lazily.)
server_version_re = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{1,2})")

# django/db/backends/mysql/base.py::CursorWrapper.codes_for_integrityerror
CODES_FOR_INTEGRITYERROR = (1048, 1690, 3819, 4025)

ISOLATION_LEVELS = frozenset(
    {"read uncommitted", "read committed", "repeatable read", "serializable"}
)

# django/db/backends/mysql/base.py::DatabaseWrapper.mysql_server_data (the same SELECT).
MYSQL_SERVER_DATA_SQL = """
                SELECT VERSION(),
                       @@sql_mode,
                       @@default_storage_engine,
                       @@sql_auto_is_null,
                       @@lower_case_table_names,
                       CONVERT_TZ('2001-01-01 01:00:00', 'UTC', 'UTC') IS NOT NULL
            """

SERVER_DATA_KEYS = (
    "version",
    "sql_mode",
    "default_storage_engine",
    "sql_auto_is_null",
    "lower_case_table_names",
    "has_zoneinfo_database",
)


def mysql_server_data_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    """The ``mysql_server_data`` dict Django builds from the row of ``MYSQL_SERVER_DATA_SQL``."""
    return {
        "version": row[0],
        "sql_mode": row[1],
        "default_storage_engine": row[2],
        "sql_auto_is_null": bool(row[3]),
        "lower_case_table_names": bool(row[4]),
        "has_zoneinfo_database": bool(row[5]),
    }


def parse_server_version(version: str) -> tuple[int, ...]:
    """``mysql_version`` for a ``VERSION()`` string (``Exception`` when it has no x.y.z part)."""
    match = server_version_re.match(version)
    if not match:
        raise Exception(f"Unable to determine MySQL version from version string {version!r}")
    return tuple(int(x) for x in match.groups())
