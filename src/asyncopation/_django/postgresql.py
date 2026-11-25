"""PostgreSQL boundary of the Django wrapper (ADR-0008).

Re-exports Django's PostgreSQL ``DatabaseWrapper`` as the base of the compile-only wrapper,
injects server info into it and supplies connection-free ``compose_sql``. Importing this module
imports psycopg *through Django* (``django.db.backends.postgresql.base`` requires it);
Asyncopation code never imports psycopg directly outside ``asyncopation.drivers``. Import it
lazily so MySQL-only installs never need psycopg. Only ``compile/`` may use ``BaseWrapper``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.postgresql.base import DatabaseWrapper as BaseWrapper
from django.db.backends.postgresql.operations import DatabaseOperations

__all__ = ["BaseWrapper", "CompileOperations", "inject_pg_version"]

# ``BaseDatabaseWrapper`` is imported so the ledger can pin the signatures of the methods the
# compile-only mixin overrides (docs/django-api-ledger.toml, drift test).

_PERCENT = re.compile(r"%(.?)", re.DOTALL)


def inject_pg_version(wrapper: BaseDatabaseWrapper, version: int) -> None:
    """Pre-fill ``pg_version`` (a ``cached_property`` that would otherwise connect)."""
    wrapper.__dict__["pg_version"] = version


class CompileOperations(DatabaseOperations):
    """PostgreSQL operations whose ``compose_sql`` needs no connection and no driver.

    Django's ``compose_sql`` renders client-side through ``mogrify``, which opens a cursor
    (``contrib.postgres`` ``SearchHeadline`` options call it while the SQL is generated, and that is
    its only caller during SQL generation). This version is plain Python and renders exactly the
    types that caller passes, byte for byte like Django's output on a live connection (checked in
    tests/integration against psycopg's compiled ``binary`` build and its pure-Python
    implementation): ``str`` (quotes doubled; a string with a backslash becomes `` E'...'`` with
    doubled backslashes, which is correct whatever ``standard_conforming_strings`` is), ``int``,
    ``float`` (``'NaN'::float8``, ``'Infinity'::float8`` and ``'-Infinity'::float8`` for the special
    values), finite ``Decimal``, ``bool`` and ``None`` (``NULL``). Finite negative numbers
    (``int``, ``float`` and ``Decimal``), signed zeros included, get a leading space.

    Only the *exact* built-in types are rendered. A subclass (``IntEnum``, ``TextChoices``, a Django
    ``Choices`` enum, ...) raises ``TypeError``: the compiled ``binary`` and the pure-Python psycopg
    implementations disagree about subclasses (they call ``__int__``, ``encode``, ``__contains__``
    or ``__str__`` of the subclass, or not), so no single rendering is the live one, and an
    overridden ``__str__`` would put arbitrary text into the SQL. Dates, ``bytes``, arrays and
    ranges depend on the driver's adapters and raise ``TypeError`` too, at build time, and so does a
    non-finite ``Decimal``: the two implementations render the NaN variants differently (the
    ``binary`` one ``-NaN`` as `` -NaN`` and ``NaN123`` as ``NaN123``, the pure-Python one every
    such value as ``'NaN'::numeric``); ``±Infinity``, plain NaN and positive sNaN come out alike in
    both (``-sNaN`` is a differing variant), and are rejected with them to keep a single rule.
    A source-built ``c`` implementation was not checked.

    ``sql`` is encoded first (a lone surrogate raises ``UnicodeEncodeError``, with or without
    ``params``). ``params`` must be exactly a list or a tuple (``None`` returns ``sql`` untouched).
    Only ``%s`` and ``%%`` are understood; the placeholder shapes and the parameter count are
    checked before any value is rendered, as psycopg does. Other placeholders and wrong parameter
    counts raise ``ValueError``; ``Mapping`` params raise ``TypeError`` (``%(name)s`` with a list is
    an unsupported placeholder, ``ValueError``). A live connection raises psycopg's
    ``ProgrammingError`` for wrong counts, a ``TypeError`` for ``%(name)s`` with a list, and
    renders ``%b`` and ``%t``: the exception classes differ, and ``%b`` and ``%t`` are not
    supported. A NUL character in a string raises ``ValueError`` (a live connection raises
    psycopg's ``DataError``; matching it would need psycopg), a lone surrogate
    ``UnicodeEncodeError`` like a live connection. A string with both raises
    ``UnicodeEncodeError`` (the ``binary`` implementation's order; the pure-Python one reports the
    NUL first).
    """

    def compose_sql(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None) -> str:
        sql.encode("utf-8")  # a lone surrogate raises UnicodeEncodeError, as on a live connection
        if params is None:
            return sql
        if isinstance(params, Mapping):
            raise TypeError("compose_sql with named placeholders is not supported")
        if type(params) not in (list, tuple):
            raise TypeError(f"compose_sql params must be a list or tuple, not {type(params)}")
        values = tuple(params)  # one snapshot for the count and the rendering
        kinds = [match.group(1) for match in _PERCENT.finditer(sql)]
        for kind in kinds:  # placeholder shape and count come first, as in psycopg
            if kind not in ("s", "%"):
                raise ValueError(f"unsupported placeholder %{kind} in {sql!r}")
        wanted = kinds.count("s")
        if len(values) < wanted:
            raise ValueError(f"not enough parameters for {sql!r}")
        if len(values) > wanted:
            raise ValueError(f"too many parameters for {sql!r}")
        rendered = iter(values)

        def render(match: re.Match[str]) -> str:
            return "%" if match.group(1) == "%" else _literal(next(rendered))

        return _PERCENT.sub(render, sql)


_FLOAT_SPECIAL = {
    "inf": "'Infinity'::float8",
    "-inf": "'-Infinity'::float8",
    "nan": "'NaN'::float8",
}


def _literal(value: object) -> str:
    kind = type(value)  # exact types only: see CompileOperations
    if value is None:
        return "NULL"
    if kind is bool:
        return "true" if value else "false"
    if kind is int and isinstance(value, int):
        return str(value) if value >= 0 else f" {value}"
    if kind is float and isinstance(value, float):
        text = str(value)
        if text in _FLOAT_SPECIAL:
            return _FLOAT_SPECIAL[text]
        return f" {text}" if text.startswith("-") else text  # also for -0.0
    if kind is Decimal and isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("compose_sql cannot render a non-finite Decimal without a driver")
        text = str(value)
        return f" {text}" if text.startswith("-") else text
    if kind is str and isinstance(value, str):
        text = str(value)
        text.encode("utf-8")  # a lone surrogate raises UnicodeEncodeError, as on a live connection
        if "\x00" in text:
            raise ValueError("PostgreSQL text cannot contain NUL (0x00) characters")
        quoted = text.replace("'", "''")
        if "\\" in quoted:
            return " E'" + quoted.replace("\\", "\\\\") + "'"
        return f"'{quoted}'"
    subclass = isinstance(value, str | int | float | Decimal)  # an exact type returned above
    note = " (only exact built-in types are rendered)" if subclass else ""
    raise TypeError(f"compose_sql cannot render {kind.__name__}{note} without a driver")
