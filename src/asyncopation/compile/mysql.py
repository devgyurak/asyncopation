"""PostgreSQL-style compile-only wrapper for MySQL and MariaDB (ADR-0008 Decision 5).

``MySQLCompileWrapper`` is Django's backend-neutral ``BaseDatabaseWrapper`` composed with
``mysql.features`` and ``mysql.operations`` (through ``asyncopation._django.mysql``) and the
``DatabaseWrapper`` attributes Django defines in ``mysql/base.py``, ported here because that module
imports ``MySQLdb``: asyncopation does not need mysqlclient. The server data Django reads with a
query (``mysql_server_data``) is injected from a frozen ``MySQLServerInfo``.
"""

# Ported from django/db/backends/mysql/base.py::DatabaseWrapper @ 6.1.3
# Changes:
# - `cached_property` is ``functools.cached_property`` (Django's ``django.utils.functional`` one is
#   deprecated on Django main and is not in the ledger's public list).
# - `mysql_server_data` is injected (``apply_server_info``) instead of read with a query.
# - `_lazy_re_compile` is a plain ``re.compile`` (``django.utils.regex_helper`` is not public API).
# - The "Unable to determine MySQL version" message is an f-string instead of ``%`` formatting
#   (same text).
#
# Members of Django's ``DatabaseWrapper`` that are NOT ported (compile-only, nothing connects):
# ``SchemaEditorClass`` and ``validation_class`` (never used while compiling), the connection-bound
# methods ``get_connection_params``, ``get_new_connection``, ``init_connection_state``,
# ``create_cursor``, ``_rollback``, ``_set_autocommit``, ``disable_constraint_checking``,
# ``enable_constraint_checking``, ``check_constraints`` and ``is_usable``. ``Database`` is a shim
# (``_Database``) and the ``*_class`` attributes name Django's own classes (``introspection_class``
# the backend-neutral stand-in). The list is pinned by ``tests/support/mysql_source_drift.py`` (it
# reads Django's source, so it needs no mysqlclient): a new Django member fails
# ``tests/unit/django_drift/test_mysql_source_drift.py`` until it is ported or listed there.

from __future__ import annotations

import re
from functools import cached_property
from typing import Any, ClassVar

from asyncopation._django.mysql import (
    BaseWrapper,
    DatabaseClient,
    DatabaseCreation,
    DatabaseFeatures,
    DatabaseIntrospection,
    DatabaseOperations,
    inject_mysql_server_data,
)
from asyncopation.compile.server_info import MySQLServerInfo, ServerInfo
from asyncopation.compile.wrapper import CompileOnlyMixin, snapshot_settings, timezone_key

server_version_re = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{1,2})")


class _Database:
    """What Django reads from ``connection.Database`` while generating SQL.

    Only ``Binary`` (``BinaryField.get_db_prep_value``); ``MySQLdb.Binary`` is ``bytes``. Not ported
    from Django (``Database`` is the ``MySQLdb`` module there), so no driver module is imported.
    """

    Binary = bytes


class MySQLCompileWrapper(CompileOnlyMixin, BaseWrapper):
    """Django's MySQL/MariaDB wrapper behaviour for SQL generation; it can never connect."""

    vendor = "mysql"
    # This dictionary maps Field objects to their associated MySQL column
    # types, as strings. Column-type strings can contain format strings;
    # they'll be interpolated against the values of Field.__dict__ before being
    # output. If a column type is set to None, it won't be included in the
    # output.

    _data_types: ClassVar[dict[str, str]] = {
        "AutoField": "integer AUTO_INCREMENT",
        "BigAutoField": "bigint AUTO_INCREMENT",
        "BinaryField": "longblob",
        "BooleanField": "bool",
        "CharField": "varchar(%(max_length)s)",
        "DateField": "date",
        "DateTimeField": "datetime(6)",
        "DecimalField": "numeric(%(max_digits)s, %(decimal_places)s)",
        "DurationField": "bigint",
        "FileField": "varchar(%(max_length)s)",
        "FilePathField": "varchar(%(max_length)s)",
        "FloatField": "double precision",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "char(15)",
        "GenericIPAddressField": "char(39)",
        "JSONField": "json",
        "PositiveBigIntegerField": "bigint UNSIGNED",
        "PositiveIntegerField": "integer UNSIGNED",
        "PositiveSmallIntegerField": "smallint UNSIGNED",
        "SlugField": "varchar(%(max_length)s)",
        "SmallAutoField": "smallint AUTO_INCREMENT",
        "SmallIntegerField": "smallint",
        "TextField": "longtext",
        "TimeField": "time(6)",
        "UUIDField": "char(32)",
    }

    @cached_property
    def data_types(self):
        _data_types = self._data_types.copy()
        if self.features.has_native_uuid_field:
            _data_types["UUIDField"] = "uuid"
        return _data_types

    # For these data types MySQL and MariaDB don't support full width database
    # indexes.
    _limited_data_types: ClassVar[tuple[str, ...]] = (
        "tinyblob",
        "blob",
        "mediumblob",
        "longblob",
        "tinytext",
        "text",
        "mediumtext",
        "longtext",
        "json",
    )

    operators: ClassVar[dict[str, str]] = {
        "exact": "= %s",
        "iexact": "LIKE %s",
        "contains": "LIKE BINARY %s",
        "icontains": "LIKE %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE BINARY %s",
        "endswith": "LIKE BINARY %s",
        "istartswith": "LIKE %s",
        "iendswith": "LIKE %s",
    }

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an
    # expression or the result of a bilateral transformation). In those cases,
    # special characters for LIKE operators (e.g. \, *, _) should be escaped on
    # database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a
    # wildcard for the LIKE operator.
    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\\', '\\\\'), '%%', '\%%'), '_', '\_')"
    pattern_ops: ClassVar[dict[str, str]] = {
        "contains": "LIKE BINARY CONCAT('%%', {}, '%%')",
        "icontains": "LIKE CONCAT('%%', {}, '%%')",
        "startswith": "LIKE BINARY CONCAT({}, '%%')",
        "istartswith": "LIKE CONCAT({}, '%%')",
        "endswith": "LIKE BINARY CONCAT('%%', {})",
        "iendswith": "LIKE CONCAT('%%', {})",
    }

    isolation_levels: ClassVar[set[str]] = {
        "read uncommitted",
        "read committed",
        "repeatable read",
        "serializable",
    }

    # injected by apply_server_info (Django reads it with a query)
    mysql_server_data: dict[str, Any]
    Database: ClassVar[type[_Database]] = _Database

    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations

    def get_database_version(self):
        return self.mysql_version

    @cached_property
    def display_name(self):
        return "MariaDB" if self.mysql_is_mariadb else "MySQL"

    @cached_property
    def data_type_check_constraints(self):
        if self.features.supports_column_check_constraints:
            check_constraints = {
                "PositiveBigIntegerField": "`%(column)s` >= 0",
                "PositiveIntegerField": "`%(column)s` >= 0",
                "PositiveSmallIntegerField": "`%(column)s` >= 0",
            }
            return check_constraints
        return {}

    @cached_property
    def mysql_server_info(self):
        return self.mysql_server_data["version"]

    @cached_property
    def mysql_version(self):
        match = server_version_re.match(self.mysql_server_info)
        if not match:
            raise Exception(
                f"Unable to determine MySQL version from version string {self.mysql_server_info!r}"
            )
        return tuple(int(x) for x in match.groups())

    @cached_property
    def mysql_is_mariadb(self):
        return "mariadb" in self.mysql_server_info.lower()

    @cached_property
    def sql_mode(self):
        sql_mode = self.mysql_server_data["sql_mode"]
        return set(sql_mode.split(",") if sql_mode else ())

    def apply_server_info(self, info: ServerInfo) -> None:
        if not isinstance(info, MySQLServerInfo):
            raise TypeError(f"expected MySQLServerInfo, got {type(info).__name__}")
        if "mysql_server_data" in self.__dict__:
            raise RuntimeError("server info is injected once per wrapper; build a new wrapper")
        inject_mysql_server_data(
            self,
            {
                "version": info.version,
                "sql_mode": info.sql_mode,
                "default_storage_engine": info.default_storage_engine,
                "sql_auto_is_null": bool(info.sql_auto_is_null),
                "lower_case_table_names": bool(info.lower_case_table_names),
                "has_zoneinfo_database": bool(info.has_zoneinfo_database),
            },
        )
        self.server_info = info

    server_info: MySQLServerInfo | None = None


_WRAPPERS: dict[str, MySQLCompileWrapper] = {}


def wrapper_for(
    alias: str, settings_dict: dict[str, Any], info: MySQLServerInfo
) -> MySQLCompileWrapper:
    """Return the one shared wrapper of ``alias``, creating it on first use.

    Same contract as ``compile.postgresql.wrapper_for``: ``settings_dict`` is the configured dict, the
    wrapper keeps a copy of its dict/list structure, and a different ``settings_dict``, ``info`` or ``(USE_TZ, TIME_ZONE)``
    yields a fresh wrapper that replaces the registered one (the derived cached properties never go
    stale). Construction performs no I/O.
    """
    wrapper = _WRAPPERS.get(alias)
    tz = timezone_key()
    if (
        wrapper is None
        or wrapper.settings_dict != settings_dict
        or wrapper.server_info != info
        or wrapper.timezone_key != tz
    ):
        wrapper = _WRAPPERS[alias] = MySQLCompileWrapper(snapshot_settings(settings_dict), alias)
        wrapper.timezone_key = tz
        wrapper.apply_server_info(info)
    return wrapper
