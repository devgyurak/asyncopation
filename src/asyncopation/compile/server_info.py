"""Server facts a compile-only wrapper needs, probed asynchronously elsewhere (ADR-0004/0008)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Base of the per-backend server info; backends add their own fields."""


@dataclass(frozen=True, slots=True)
class PostgreSQLServerInfo(ServerInfo):
    """``pg_version`` as psycopg reports it, e.g. ``170002`` for 17.2."""

    pg_version: int


@dataclass(frozen=True, slots=True)
class MySQLServerInfo(ServerInfo):
    """The six values Django reads once per server into ``DatabaseWrapper.mysql_server_data``.

    ``version`` is ``SELECT VERSION()`` (``"8.4.3"``, ``"11.8.1-MariaDB-ubu2404"``), ``sql_mode`` the
    raw ``@@sql_mode`` string. The flags are what Django makes of the integers ``@@sql_auto_is_null``
    and ``@@lower_case_table_names`` and of ``CONVERT_TZ(...) IS NOT NULL``; the wrapper applies
    ``bool()`` to them, like Django does with the row.

    The #7 probe must pass ``VERSION()`` exactly as Django's own query gets it, not the version
    string of the connection handshake: a MariaDB handshake reports ``"5.5.5-10.11.6-MariaDB"``
    (a replication compatibility prefix), which Django's pattern reads as 5.5.5 (checked by
    ``tests/unit/compile/test_mysql_wrapper_injection.py``).

    Field order contract: the fields are in the order of the columns of Django's query
    (``VERSION(), @@sql_mode, @@default_storage_engine, @@sql_auto_is_null,
    @@lower_case_table_names, CONVERT_TZ(...) IS NOT NULL``), so the probe can build the info
    as ``MySQLServerInfo(*row)``; ``tests/support/mysql_source_drift.py`` pins that order to the
    ``row[n]`` indexes in Django's ``mysql_server_data``.
    """

    version: str
    sql_mode: str
    default_storage_engine: str
    sql_auto_is_null: bool
    lower_case_table_names: bool
    has_zoneinfo_database: bool
