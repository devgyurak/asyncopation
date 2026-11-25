"""Which psycopg implementation a run expects (`ASYNCOPATION_EXPECT_PSYCOPG_IMPL`) and which loaded.

The expectation is a separate variable from psycopg's own `PSYCOPG_IMPL`: psycopg already refuses
to fall back when `PSYCOPG_IMPL` names an implementation it cannot load, but a run that lost the
`PSYCOPG_IMPL` variable would silently use the binary one. The Makefile target sets both.
"""

from __future__ import annotations

import os


def expected_implementation() -> str | None:
    value = os.environ.get("ASYNCOPATION_EXPECT_PSYCOPG_IMPL")
    return value.lower() if value else None


def loaded_implementation() -> str:
    import psycopg

    return psycopg.pq.__impl__
