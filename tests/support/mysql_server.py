"""Server data values and Django's own parsing, for the MySQL/MariaDB unit tests."""

from __future__ import annotations

from typing import Any

MYSQL_8_4: dict[str, Any] = {
    "version": "8.4.11",
    "sql_mode": "ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE",
    "default_storage_engine": "InnoDB",
    "sql_auto_is_null": False,
    "lower_case_table_names": False,
    "has_zoneinfo_database": True,
}
MARIADB_11_8: dict[str, Any] = {
    "version": "11.8.9-MariaDB-ubu2404",
    "sql_mode": "STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER",
    "default_storage_engine": "InnoDB",
    "sql_auto_is_null": False,
    "lower_case_table_names": False,
    "has_zoneinfo_database": True,
}


def server_data(values: dict[str, Any] | None = None) -> Any:
    from asyncopation.drivers.mysql.server_data import MySQLServerData

    return MySQLServerData(**(values or MYSQL_8_4))


def django_parse(version: str) -> tuple[int, ...]:
    """`mysql_version` of Django's real wrapper for a `VERSION()` string."""
    from django.db.backends.mysql.base import DatabaseWrapper

    settings: Any = {"NAME": "x", "OPTIONS": {}}
    wrapper = DatabaseWrapper(settings, "default")
    wrapper.__dict__["mysql_server_data"] = {**MYSQL_8_4, "version": version}
    version_tuple: Any = wrapper.mysql_version
    return tuple(version_tuple)
