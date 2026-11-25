"""PostgreSQL adapter boundary: Django's psycopg3 helpers the driver reuses (ADR-0007).

The only module that may import Django's adapter helpers (guard A104) and it is imported only by
``drivers/postgresql``. Kept apart from ``_django.postgresql`` (the compile-only wrapper boundary,
which imports Django's PostgreSQL backend at import time).

Importing this module needs neither psycopg nor Django's PostgreSQL backend: each helper imports
Django's ``psycopg_any`` on first call, so MySQL-only installs never load psycopg.
"""

from __future__ import annotations

from typing import Any

__all__ = ["adapters_template"]


def adapters_template(use_tz: bool, timezone: Any) -> Any:
    """Django's psycopg3 adapters template for ``(USE_TZ, tzinfo)`` (cached by Django).

    Registers the text loaders Django expects (``jsonb``/``inet``/``cidr`` stay strings, range
    dumper) and the time-zone aware ``timestamptz`` loader. Pure: builds an ``AdaptersMap``, no I/O.
    """
    from django.db.backends.postgresql.psycopg_any import get_adapters_template  # ty: ignore[import-not-found]

    return get_adapters_template(use_tz, timezone)
