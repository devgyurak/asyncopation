"""PostgreSQL boundary: Django's psycopg3 helpers the driver reuses (ADR-0007).

Imported lazily by ``drivers.postgresql`` so MySQL-only installs never need psycopg.
"""

from __future__ import annotations

from typing import Any

__all__ = ["adapters_template", "ported_references"]


def adapters_template(use_tz: bool, timezone: Any) -> Any:
    """Django's psycopg3 adapters template for ``(USE_TZ, tzinfo)`` (cached by Django).

    Registers the text loaders Django expects (``jsonb``/``inet``/``cidr`` stay strings, range
    dumper) and the time-zone aware ``timestamptz`` loader. Pure: builds an ``AdaptersMap``, no I/O.
    """
    from django.db.backends.postgresql.psycopg_any import get_adapters_template  # ty: ignore[import-not-found]

    return get_adapters_template(use_tz, timezone)


def ported_references() -> dict[str, Any]:
    """Upstream objects the PostgreSQL driver ports (never called at runtime).

    ``drivers/errors.py`` ports ``DatabaseErrorWrapper``; ``drivers/postgresql/settings.py`` ports
    ``DatabaseWrapper.get_connection_params``; ``drivers/postgresql/connection.py`` ports
    ``ServerSideCursor`` and ``create_cursor``; ``resolve_timezone`` ports ``BaseDatabaseWrapper
    .timezone`` and ``quote_name`` is ``DatabaseOperations.quote_name``. The drift tests
    (``tests/unit/drivers``) compare behaviour against these.
    """
    from django.db.backends.base.base import BaseDatabaseWrapper
    from django.db.backends.postgresql.base import DatabaseWrapper, ServerSideCursor  # ty: ignore[missing-module-member]
    from django.db.backends.postgresql.operations import DatabaseOperations
    from django.db.utils import DatabaseErrorWrapper

    return {
        "BaseDatabaseWrapper": BaseDatabaseWrapper,
        "DatabaseErrorWrapper": DatabaseErrorWrapper,
        "DatabaseOperations": DatabaseOperations,
        "DatabaseWrapper": DatabaseWrapper,
        "ServerSideCursor": ServerSideCursor,
    }
