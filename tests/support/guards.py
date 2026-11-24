"""Helpers for testing tools/guards."""

from __future__ import annotations

import textwrap

from tools.guards import archlint, langlint, testlint
from tools.guards.archlint import Ledger

EMPTY_LEDGER = Ledger(
    public=frozenset({"django.db", "django.db.models", "django.conf"}), internal=frozenset()
)


def lint_test_module(source: str, path: str = "tests/unit/test_sample.py") -> list[str]:
    return sorted(v.code for v in testlint.check(path, textwrap.dedent(source)))


def lint_src_module(
    source: str, path: str = "src/asyncopation/query/sample.py", ledger: Ledger = EMPTY_LEDGER
) -> list[str]:
    return sorted(v.code for v in archlint.check(path, textwrap.dedent(source), ledger))


def lint_language(source: str, path: str = "src/asyncopation/sample.py") -> list[str]:
    return sorted(v.code for v in langlint.check(path, textwrap.dedent(source)))
