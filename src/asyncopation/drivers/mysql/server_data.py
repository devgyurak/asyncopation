"""The server facts the asyncmy driver reads once per ``open()`` (ADR-0003 Decision 2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asyncopation._django.mysql import mysql_server_data_from_row, parse_server_version


@dataclass(frozen=True, slots=True)
class MySQLServerData:
    """Django's ``mysql_server_data`` dict as an immutable value (MySQL and MariaDB)."""

    version: str
    sql_mode: str
    default_storage_engine: str
    sql_auto_is_null: bool
    lower_case_table_names: bool
    has_zoneinfo_database: bool

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> MySQLServerData:
        """Build from the row of Django's ``mysql_server_data`` ``SELECT``."""
        return cls(**mysql_server_data_from_row(row))

    @property
    def server_version(self) -> tuple[int, ...]:
        """``mysql_version``: the numeric part of ``VERSION()`` (``Capabilities.server_version``)."""
        return parse_server_version(self.version)
