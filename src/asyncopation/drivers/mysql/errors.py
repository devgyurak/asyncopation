"""asyncmy exception translation and disconnect detection (ADR-0003 Decision 4)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import asyncmy.errors
from django.core.exceptions import ImproperlyConfigured
from django.db import Error as DjangoError
from django.db import IntegrityError, OperationalError, ProgrammingError

from asyncopation._django.mysql import CODES_FOR_INTEGRITYERROR
from asyncopation.drivers.errors import raise_mapped

# 1053 server shutdown, 2006 server has gone away, 2013 lost connection during query, 2055 lost
# connection (with the address), 1927 connection killed (MariaDB), 3169 session terminated by a
# privileged user (MySQL), 2014 commands out of sync (the protocol state is unknown, so the
# connection cannot be reused).
_DISCONNECT_CODES = frozenset({1053, 1927, 2006, 2013, 2014, 2055, 3169})


# mysqlclient classifies server errors by errno in `_mysql.c` (`_mysql_Exception`); asyncmy has its
# own table and the two disagree for these codes (found by raising each errno from a stored
# procedure through both drivers, mysqlclient 2.3.2; a differential test repeats it). The sync
# classes win, because users catch them.
_ERRNO_CLASSES: dict[int, type[DjangoError]] = {
    1166: OperationalError,  # ER_WRONG_COLUMN_NAME (asyncmy: ProgrammingError)
    1169: IntegrityError,  # ER_DUP_UNIQUE (asyncmy: OperationalError)
    1263: OperationalError,  # WARN_DATA_TRUNCATED as an error (asyncmy: DataError)
    1364: IntegrityError,  # ER_NO_DEFAULT_FOR_FIELD (asyncmy: OperationalError)
    1366: OperationalError,  # ER_TRUNCATED_WRONG_VALUE_FOR_FIELD (asyncmy: DataError)
    1367: OperationalError,  # ER_ILLEGAL_VALUE_FOR_TYPE (asyncmy: DataError)
    2014: ProgrammingError,  # CR_COMMANDS_OUT_OF_SYNC (asyncmy: OperationalError)
}


@contextmanager
def translate_errors() -> Generator[None]:
    """Map asyncmy exceptions raised in the block to the ``django.db`` hierarchy.

    Ported from django/db/backends/mysql/base.py::CursorWrapper.execute @ 6.1.3: an
    ``OperationalError`` carrying one of ``codes_for_integrityerror`` becomes ``IntegrityError``;
    the ordinary MRO mapping applies to everything else. asyncmy's ``RuntimeError`` for a missing
    ``cryptography`` package (needed by ``caching_sha2_password``) becomes ``ImproperlyConfigured``.
    """
    # Ported from django/db/backends/mysql/base.py::CursorWrapper.execute @ 6.1.3
    # Changes: awaited driver, applied around every statement type; the remap is a catch clause.
    try:
        yield
    except asyncmy.errors.Error as exc:
        code = exc.args[0] if exc.args else None
        if code in _ERRNO_CLASSES:
            raise _ERRNO_CLASSES[code](*exc.args).with_traceback(exc.__traceback__) from exc
        if isinstance(exc, asyncmy.errors.OperationalError) and code in CODES_FOR_INTEGRITYERROR:
            raise IntegrityError(*exc.args).with_traceback(exc.__traceback__) from exc
        raise_mapped(exc, asyncmy.errors)
    except RuntimeError as exc:
        if "cryptography" in str(exc):
            raise ImproperlyConfigured(
                f"{exc} Install it with `pip install cryptography` "
                "(or `pip install django-asyncopation[mysql]`)."
            ) from exc
        raise
    except Exception as exc:
        raise_mapped(exc, asyncmy.errors)


def is_disconnect(exc: BaseException) -> bool:
    """True when ``exc`` (raw asyncmy, or its mapped Django form) means the connection is gone.

    The pool-timeout ``OperationalError`` is raised by the driver itself and has no asyncmy cause.
    """
    raw = exc if isinstance(exc, asyncmy.errors.Error) else exc.__cause__
    if not isinstance(raw, asyncmy.errors.Error):
        return False
    if isinstance(raw, asyncmy.errors.InterfaceError):
        return (
            True  # closed / cancelled connection ("Cancelled during execution", "Already closed")
        )
    return isinstance(raw, asyncmy.errors.OperationalError) and (
        bool(raw.args) and raw.args[0] in _DISCONNECT_CODES
    )
