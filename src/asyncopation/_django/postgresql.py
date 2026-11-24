"""PostgreSQL boundary: Django's psycopg3 helpers the driver reuses (ADR-0007).

Imported lazily by ``drivers.postgresql`` so MySQL-only installs never need psycopg.
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
