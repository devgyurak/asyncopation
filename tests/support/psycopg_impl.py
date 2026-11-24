"""Which psycopg implementation was requested (`PSYCOPG_IMPL`) and which one is loaded."""

from __future__ import annotations

import os


def requested_implementation() -> str | None:
    return os.environ.get("PSYCOPG_IMPL") or None


def loaded_implementation() -> str:
    import psycopg

    return psycopg.pq.__impl__
