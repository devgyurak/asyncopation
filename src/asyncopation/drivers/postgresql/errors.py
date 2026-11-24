"""psycopg3 exception translation and disconnect detection."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import psycopg
import psycopg_pool

from asyncopation.drivers.errors import wrap_database_errors

# SQLSTATE class 08 (connection exception) and admin/crash shutdown (57P01-57P03).
_DISCONNECT_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
_POOL_ERRORS = (psycopg_pool.PoolTimeout, psycopg_pool.PoolClosed, psycopg_pool.TooManyRequests)


@contextmanager
def translate_errors() -> Generator[None]:
    """Map psycopg exceptions raised in the block to the ``django.db`` hierarchy."""
    with wrap_database_errors(psycopg):
        yield


def is_disconnect(exc: BaseException) -> bool:
    """True when ``exc`` (raw psycopg, or its mapped Django form) means the connection is gone."""
    raw = exc if isinstance(exc, psycopg.Error) else exc.__cause__
    if not isinstance(raw, psycopg.OperationalError) or isinstance(raw, _POOL_ERRORS):
        return False
    sqlstate = raw.sqlstate
    # No SQLSTATE: the failure happened client side (connection closed / lost mid-protocol).
    return sqlstate is None or sqlstate.startswith("08") or sqlstate in _DISCONNECT_SQLSTATES
