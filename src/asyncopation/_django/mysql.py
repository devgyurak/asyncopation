"""MySQL / MariaDB boundary of the Django wrapper (ADR-0008 Decision 5).

Re-exports the pieces of Django's MySQL backend that the compile-only wrapper is composed from,
and injects the server data into it. Nothing here imports ``django.db.backends.mysql.base`` or
``MySQLdb``: Django 6.1's ``mysql.features``, ``mysql.operations``, ``mysql.client`` and
``mysql.creation`` do not either (``mysql.introspection`` and ``mysql.base`` do, so the backend-neutral
base introspection stands in), so MySQL support never needs mysqlclient. Import it lazily. Only
``compile/`` may use ``BaseWrapper``.
"""

from __future__ import annotations

from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper as BaseWrapper
from django.db.backends.base.introspection import BaseDatabaseIntrospection
from django.db.backends.mysql.client import DatabaseClient
from django.db.backends.mysql.creation import DatabaseCreation
from django.db.backends.mysql.features import DatabaseFeatures
from django.db.backends.mysql.operations import DatabaseOperations

__all__ = [
    "BaseWrapper",
    "DatabaseClient",
    "DatabaseCreation",
    "DatabaseFeatures",
    "DatabaseIntrospection",
    "DatabaseOperations",
    "inject_mysql_server_data",
]

DatabaseIntrospection = BaseDatabaseIntrospection
"""Stand-in for ``mysql.introspection.DatabaseIntrospection`` (which imports MySQLdb)."""


def inject_mysql_server_data(wrapper: Any, data: dict[str, Any]) -> None:
    """Pre-fill ``mysql_server_data`` (a ``cached_property`` that would otherwise connect)."""
    wrapper.__dict__["mysql_server_data"] = data
