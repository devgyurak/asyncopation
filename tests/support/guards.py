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
PUBLIC_EXTRA_LEDGER = Ledger(
    public=EMPTY_LEDGER.public | {"django.db.models.signals"}, internal=frozenset()
)
IMPORT_ONLY_LEDGER = Ledger(public=EMPTY_LEDGER.public, internal=ITERABLE_LEDGER.internal)
FETCH_MODE_LEDGER = Ledger(
    public=EMPTY_LEDGER.public, internal=frozenset(), attributes=ITERABLE_LEDGER.attributes
)
PORTED_LEDGER = Ledger(
    public=EMPTY_LEDGER.public,
    internal=frozenset({("django.db.backends.mysql.base", "DatabaseWrapper")}),
    ported=frozenset({("django.db.backends.mysql.base", "DatabaseWrapper")}),
    ported_used_by=frozenset({("django.db.backends.mysql.base", "src/asyncopation/_django/x.py")}),
)
PORTED_PATH = "src/asyncopation/_django/x.py"


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


def stale_codes(messages: list[str]) -> set[str]:
    """The guard codes that start `stale_messages` output."""
    return {message.split(" ", 1)[0] for message in messages}


PUBLIC_DJANGO_LEDGER = Ledger(
    public=EMPTY_LEDGER.public
    | {"django", "django.db.models.query", "django.db.models.base", "django.db.backends"},
    internal=frozenset(),
)


def distinct_codes(source: str, path: str) -> list[str]:
    """Distinct guard codes for `source` (a violation may be reported more than once)."""
    return sorted(set(lint_src_module(source, path=path, ledger=PUBLIC_DJANGO_LEDGER)))


MODELS = "from django.db import models\n\n"
MODEL_CLASS = "from django.db.models import Model\n\n"

_EXEMPT_HEAD = (
    "def _snapshot(value):\n    return value\n\n\n"
    "from contextlib import contextmanager\n\n"
    "def _build():\n"
    "    from django.db import connections\n\n"
    "    @contextmanager\n"
    "    def swapped(alias, wrapper):\n"
)
_EXEMPT_TAIL = "        yield\n\n    return swapped\n"


def in_function(body: str) -> str:
    """Wrap `body` in the real shape of the exempt module: `_build()` -> `swapped(alias, wrapper)`."""
    indented = "".join(f"        {line}\n" for line in body.splitlines())
    return _EXEMPT_HEAD + indented + _EXEMPT_TAIL


PEEK = "x = getattr(connections._connections, alias, None)"


def with_peek(*lines: str) -> str:
    """`in_function` body that first peeks into `x`, then runs `lines`."""
    return in_function("\n".join((PEEK, *lines)))


def violations(source: str, path: str, prefix: int = 48) -> list[tuple[str, str]]:
    """Distinct (code, message prefix) pairs, to tell apart rules that share a code."""
    found = archlint.check(path, textwrap.dedent(source), PUBLIC_DJANGO_LEDGER)
    return sorted({(v.code, v.message[:prefix]) for v in found})


def has_violation(source: str, path: str, code: str, fragment: str) -> bool:
    """Whether `source` yields a `code` violation whose message contains `fragment`."""
    found = archlint.check(path, textwrap.dedent(source), PUBLIC_DJANGO_LEDGER)
    return any(v.code == code and fragment in v.message for v in found)


def rule_outcome(source: str, path: str, code: str, fragment: str) -> tuple[list[str], bool]:
    """(distinct codes, whether a `code` violation mentions `fragment`): pins code AND rule."""
    return distinct_codes(source, path), has_violation(source, path, code, fragment)


def driver_forms(module: str, name: str) -> dict[str, str]:
    """The ways to reach ``module.name``: from-import, attribute, constant ``getattr``, and the
    attribute of a submodule taken from its package with a constant ``getattr``."""
    parent, _, last = module.rpartition(".")
    return {
        "import": f"from {module} import {name}\n",
        "attribute": f"import {module} as m\n\ndef f():\n    return m.{name}\n",
        "getattr": f"import {module} as m\n\ndef f():\n    return getattr(m, {name!r})\n",
        "getattr-chain": (
            f"import {parent} as m\n\ndef f():\n    return getattr(m, {last!r}).{name}\n"
        ),
    }


def same_verdict(flagged: bool) -> dict[str, bool]:
    """The expected ``driver_form_verdicts`` when every form agrees."""
    return dict.fromkeys(driver_forms("a.b", "c"), flagged)


def driver_form_verdicts(module: str, name: str, path: str) -> dict[str, bool]:
    """Per form, whether an A104 violation is reported for ``module.name`` in ``path``."""
    return {
        form: has_violation(source, path, "A104", "")
        for form, source in driver_forms(module, name).items()
    }


CONNECTIONS_SRC = "src/asyncopation/_django/connections.py"


def real_connections_module() -> str:
    """The repository's own `_django/connections.py` source."""
    from pathlib import Path

    return (Path(__file__).parents[2] / CONNECTIONS_SRC).read_text()


def reader_with(*lines: str) -> str:
    """A `configured_databases` reader whose body is `lines` after the `connections` import."""
    head = "def configured_databases():\n    from django.db import connections\n\n"
    return head + "".join(f"    {x}\n" for x in lines)


def real_module_codes(path: str) -> list[str]:
    """Guard codes the repository's own module at `path` triggers under the real ledger."""
    from pathlib import Path

    source = (Path(__file__).parents[2] / path).read_text()
    return lint_src_module(source, path=path, ledger=archlint.Ledger.load())


def with_mysqlclient_ban(codes: list[str], module: str) -> list[str]:
    """`codes` plus A101 when `module` is a Django MySQL backend module (it imports MySQLdb)."""
    banned = ("backends.mysql.base", "backends.mysql.introspection")
    return sorted({*codes, "A101"}) if any(b in module for b in banned) else codes
