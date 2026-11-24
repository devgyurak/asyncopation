"""Helpers for testing tools/guards."""

from __future__ import annotations

import textwrap

from tools.guards import archlint, langlint, testlint
from tools.guards.archlint import Ledger

EMPTY_LEDGER = Ledger(
    public=frozenset({"django.db", "django.db.models", "django.conf"}), internal=frozenset()
)
SQL_QUERY_LEDGER = Ledger(
    public=EMPTY_LEDGER.public, internal=frozenset({("django.db.models.sql", "Query")})
)
ITERABLE_LEDGER = Ledger(
    public=EMPTY_LEDGER.public,
    internal=frozenset({("django.db.models.query", "ModelIterable")}),
    attributes=frozenset({("django.db.models.query.QuerySet", "_fetch_mode")}),
)
IMPORT_ONLY_LEDGER = Ledger(public=EMPTY_LEDGER.public, internal=ITERABLE_LEDGER.internal)
FETCH_MODE_LEDGER = Ledger(
    public=EMPTY_LEDGER.public, internal=frozenset(), attributes=ITERABLE_LEDGER.attributes
)
PORTED_LEDGER = Ledger(
    public=EMPTY_LEDGER.public,
    internal=frozenset({("django.db.backends.mysql.base", "DatabaseWrapper")}),
    ported=frozenset({("django.db.backends.mysql.base", "DatabaseWrapper")}),
)


def lint_test_module(source: str, path: str = "tests/unit/test_sample.py") -> list[str]:
    return sorted(v.code for v in testlint.check(path, textwrap.dedent(source)))


def lint_src_module(
    source: str, path: str = "src/asyncopation/query/sample.py", ledger: Ledger = EMPTY_LEDGER
) -> list[str]:
    return sorted(v.code for v in archlint.check(path, textwrap.dedent(source), ledger))


def lint_language(source: str, path: str = "src/asyncopation/sample.py") -> list[str]:
    return sorted(v.code for v in langlint.check(path, textwrap.dedent(source)))


def stale_messages(sources: dict[str, str], ledger: Ledger) -> list[str]:
    violations = archlint.stale_ledger_entries(
        {path: textwrap.dedent(src) for path, src in sources.items()}, ledger
    )
    return [f"{v.code} {v.message}" for v in violations]
