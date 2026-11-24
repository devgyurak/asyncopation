"""Driver exception translation with ``DatabaseErrorWrapper`` semantics (ADR-0003 Decision 4).

Vendor-neutral: a driver passes in the exception classes of its DB-API module. No driver library
is imported here.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from types import ModuleType
from typing import NoReturn

from django.db import (
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

# Ported from django/db/utils.py::DatabaseErrorWrapper.__exit__ @ 6.1.3
# Changes: the wrapper/`errors_occurred` bookkeeping is dropped; the walk is a plain function over
# the driver's DB-API module so it also works for exceptions raised outside a `with` block.
# The order matters: the first Django class whose driver counterpart matches wins.
_DJANGO_ERRORS: tuple[type[Error], ...] = (
    DataError,
    OperationalError,
    IntegrityError,
    InternalError,
    ProgrammingError,
    NotSupportedError,
    DatabaseError,
    InterfaceError,
    Error,
)


def map_exception(exc: BaseException, dbapi: ModuleType) -> Error | None:
    """The ``django.db`` exception for a driver exception, or ``None`` if it is not one."""
    for dj_exc_type in _DJANGO_ERRORS:
        db_exc_type = getattr(dbapi, dj_exc_type.__name__, None)
        if db_exc_type is not None and isinstance(exc, db_exc_type):
            return dj_exc_type(*exc.args)
    return None


def raise_mapped(exc: BaseException, dbapi: ModuleType) -> NoReturn:
    """Re-raise ``exc`` as its Django class (``__cause__`` = the driver exception)."""
    mapped = map_exception(exc, dbapi)
    if mapped is None:
        raise exc
    raise mapped.with_traceback(exc.__traceback__) from exc


@contextmanager
def wrap_database_errors(dbapi: ModuleType) -> Generator[None]:
    """``with`` block translating driver exceptions; everything else passes through."""
    try:
        yield
    except Exception as exc:
        raise_mapped(exc, dbapi)
