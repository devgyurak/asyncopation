import pytest

from tests.support.guards import (
    EMPTY_LEDGER,
    FETCH_MODE_LEDGER,
    IMPORT_ONLY_LEDGER,
    PORTED_LEDGER,
    PORTED_PATH,
    PUBLIC_EXTRA_LEDGER,
    SQL_QUERY_LEDGER,
    lint_src_module,
    stale_codes,
    stale_messages,
)
from tools.guards.archlint import Ledger


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from asgiref.sync import sync_to_async\n", ["A101"]),
        ("import threading\n", ["A101"]),
        ("from django.db import transaction\n", ["A102"]),
        ("from django.db import connections\n", ["A102"]),
        ("import time\ntime.sleep(1)\n", ["A103"]),
        ("import asyncio\nasyncio.run(None)\n", ["A103"]),
        ("loop = None\nloop.run_until_complete(None)\n", ["A103"]),
        ("import psycopg\n", ["A104"]),
        ("from django.db.models.query import ModelIterable\n", ["A201", "A203"]),
        ("import django.db.models.sql\n", ["A201", "A203"]),
    ],
    ids=[
        "sync_to_async",
        "threading",
        "transaction",
        "connections",
        "sleep",
        "asyncio-run",
        "run-until-complete",
        "driver-outside-drivers",
        "unledgered-private-import",
        "plain-import-of-private-module",
    ],
)
def test_forbidden_patterns_in_src_are_reported(source, expected):
    # when
    codes = lint_src_module(source)
    # then
    assert codes == expected


def test_driver_import_inside_drivers_is_allowed():
    # given
    source = "import psycopg\n"
    # when
    codes = lint_src_module(source, path="src/asyncopation/drivers/postgresql/pool.py")
    # then
    assert codes == []


def test_async_code_in_compile_layer_is_reported():
    # given
    source = "async def compile_sql():\n    return None\n"
    # when
    codes = lint_src_module(source, path="src/asyncopation/compile/wrapper.py")
    # then
    assert codes == ["A105"]


def test_ledgered_internal_import_is_allowed():
    # given
    ledger = Ledger(
        public=EMPTY_LEDGER.public,
        internal=frozenset({("django.db.models.query", "ModelIterable")}),
    )
    # when
    codes = lint_src_module(
        "from django.db.models.query import ModelIterable\n",
        path="src/asyncopation/_django/x.py",
        ledger=ledger,
    )
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "ledger", "expected"),
    [
        ("from django.db.models.sql import Query\n", SQL_QUERY_LEDGER, ["A203"]),
        ("from django.db.models.sql import Query\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("import django.db.models.sql\n", SQL_QUERY_LEDGER, ["A201", "A203"]),
        ("from django.db.models import Q\n", EMPTY_LEDGER, []),
        ("import django\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("from django.db.models import sql\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("from django.db.models import sql as s\n", SQL_QUERY_LEDGER, ["A201", "A203"]),
        ("from django import db\n", EMPTY_LEDGER, ["A102"]),
        ("from django.db.models.query import QuerySet\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("from django.db.models.sql import QUERY\n", SQL_QUERY_LEDGER, ["A201", "A203"]),
        ("from django.db.models import query\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("from django.db.models.deletion import Collector\n", EMPTY_LEDGER, ["A201", "A203"]),
        ("from django.db.models.signals import post_save\n", PUBLIC_EXTRA_LEDGER, []),
    ],
    ids=[
        "ledgered-outside",
        "unledgered-outside",
        "plain-import",
        "public-module",
        "bare-django",
        "private-submodule-as-name",
        "private-submodule-aliased",
        "public-submodule-as-name",
        "private-module-from-import",
        "name-differing-from-submodule-only-by-case",
        "private-py-submodule-as-name",
        "deletion-is-private",
        "signals-is-public",
    ],
)
def test_private_django_import_outside_the_django_package_is_classified(source, ledger, expected):
    # when
    codes = lint_src_module(source, path="src/asyncopation/query/x.py", ledger=ledger)
    # then
    assert codes == expected


@pytest.mark.parametrize(
    "path",
    ["src/asyncopation/_django/x.py", "src/asyncopation/_django/postgresql/x.py"],
    ids=["package", "subpackage"],
)
def test_ledgered_private_import_inside_the_django_package_is_allowed(path):
    # when
    codes = lint_src_module(
        "from django.db.models.sql import Query\n", path=path, ledger=SQL_QUERY_LEDGER
    )
    # then
    assert codes == []


def test_unledgered_private_import_inside_the_django_package_is_reported_as_a201_only():
    # when
    codes = lint_src_module(
        "from django.db.models.sql import Query\n", path="src/asyncopation/_django/x.py"
    )
    # then
    assert codes == ["A201"]


def test_internal_entry_imported_only_outside_the_django_package_is_stale():
    # given
    sources = {"src/asyncopation/query/x.py": "from django.db.models.query import ModelIterable\n"}
    # when
    stale = stale_messages(sources, IMPORT_ONLY_LEDGER)
    # then
    assert len(stale) == 1
    assert stale[0].startswith("A202")
    assert "django.db.models.query.ModelIterable" in stale[0]


def test_internal_entry_imported_in_the_django_package_is_not_stale():
    # given
    sources = {
        "src/asyncopation/_django/x.py": "from django.db.models.query import ModelIterable\n"
    }
    # when
    stale = stale_messages(sources, IMPORT_ONLY_LEDGER)
    # then
    assert stale == []


def test_attribute_entry_never_accessed_is_stale():
    # when
    stale = stale_messages({"src/asyncopation/query/y.py": "x = 1\n"}, FETCH_MODE_LEDGER)
    # then
    assert len(stale) == 1
    assert "QuerySet._fetch_mode" in stale[0]


@pytest.mark.parametrize(
    "source",
    [
        "def f(qs):\n    return qs._fetch_mode\n",
        "def f(qs):\n    return getattr(qs, '_fetch_mode')\n",
    ],
    ids=["attribute-access", "getattr-string"],
)
def test_attribute_entry_used_anywhere_in_src_is_not_stale(source):
    # when
    stale = stale_messages({"src/asyncopation/query/y.py": source}, FETCH_MODE_LEDGER)
    # then
    assert stale == []


@pytest.mark.parametrize(
    "source",
    ["import django.db.models.query\n", "from django.db.models import query\n"],
    ids=["plain-module-import", "module-as-name"],
)
def test_module_import_under_the_django_package_keeps_named_entries_alive(source):
    # when
    stale = stale_messages({"src/asyncopation/_django/x.py": source}, IMPORT_ONLY_LEDGER)
    # then
    assert stale == []


def test_star_entry_is_kept_alive_by_a_from_import_under_the_django_package():
    # given
    ledger = Ledger(
        public=EMPTY_LEDGER.public, internal=frozenset({("django.db.models.query", "*")})
    )
    sources = {"src/asyncopation/_django/x.py": "from django.db.models.query import QuerySet\n"}
    # when
    stale = stale_messages(sources, ledger)
    # then
    assert stale == []


def test_third_party_import_under_the_django_package_keeps_its_entry_alive():
    # given
    ledger = Ledger(public=EMPTY_LEDGER.public, internal=frozenset({("asgiref.local", "Local")}))
    sources = {"src/asyncopation/_django/x.py": "from asgiref.local import Local\n"}
    # when
    stale = stale_messages(sources, ledger)
    # then
    assert stale == []


def test_ported_entry_with_a_ported_from_comment_is_not_stale():
    # given
    sources = {PORTED_PATH: "# Ported from django/db/backends/mysql/base.py::X @ 6.1.3\nx = 1\n"}
    # when
    stale = stale_messages(sources, PORTED_LEDGER)
    # then
    assert stale == []


def test_ported_entry_with_a_package_header_is_not_stale():
    # given
    ledger = Ledger(
        public=EMPTY_LEDGER.public,
        internal=frozenset({("django.db.backends.mysql", "X")}),
        ported=frozenset({("django.db.backends.mysql", "X")}),
        ported_used_by=frozenset({("django.db.backends.mysql", PORTED_PATH)}),
    )
    sources = {PORTED_PATH: "# Ported from django/db/backends/mysql/__init__.py::X @ 6.1.3\n"}
    # when
    stale = stale_messages(sources, ledger)
    # then
    assert stale == []


def test_ported_entry_whose_used_by_file_is_missing_is_stale():
    # when
    stale = stale_messages({"src/asyncopation/query/y.py": "x = 1\n"}, PORTED_LEDGER)
    # then
    assert len(stale) == 1
    assert stale[0].startswith("A202")
    assert PORTED_PATH in stale[0]
    assert "does not exist" in stale[0]


def test_ported_entry_used_by_a_path_outside_src_is_stale():
    # given
    ledger = Ledger(
        public=EMPTY_LEDGER.public,
        internal=PORTED_LEDGER.internal,
        ported=PORTED_LEDGER.ported,
        ported_used_by=frozenset({("django.db.backends.mysql.base", "tests/support/x.py")}),
    )
    sources = {"tests/support/x.py": "# Ported from django/db/backends/mysql/base.py::X\n"}
    # when
    stale = stale_messages(sources, ledger)
    # then
    assert len(stale) == 1
    assert "is not a .py file under src/asyncopation/" in stale[0]


@pytest.mark.parametrize(
    "path",
    ["src/asyncopation/notes.txt", "src/other/x.py", "src/asyncopation_x/x.py"],
    ids=["not-python", "other-package", "sibling-prefix"],
)
def test_ported_entry_used_by_a_non_package_or_non_python_path_is_stale(path):
    # given
    ledger = Ledger(
        public=EMPTY_LEDGER.public,
        internal=PORTED_LEDGER.internal,
        ported=PORTED_LEDGER.ported,
        ported_used_by=frozenset({("django.db.backends.mysql.base", path)}),
    )
    # when
    stale = stale_messages({path: "# Ported from django/db/backends/mysql/base.py\n"}, ledger)
    # then
    assert len(stale) == 1
    assert "is not a .py file under src/asyncopation/" in stale[0]


def test_ported_entry_with_several_used_by_files_reports_each_failing_one():
    # given
    base = "django.db.backends.mysql.base"
    ledger = Ledger(
        public=EMPTY_LEDGER.public,
        internal=PORTED_LEDGER.internal,
        ported=PORTED_LEDGER.ported,
        ported_used_by=frozenset(
            {
                (base, PORTED_PATH),
                (base, "src/asyncopation/_django/y.py"),
                (base, "src/asyncopation/_django/z.py"),
            }
        ),
    )
    sources = {
        PORTED_PATH: "# Ported from django/db/backends/mysql/base.py\n",
        "src/asyncopation/_django/y.py": "y = 1\n",
    }
    # when
    stale = stale_messages(sources, ledger)
    # then
    assert len(stale) == 2
    assert "_django/y.py" in stale[0]
    assert "_django/z.py" in stale[1]


@pytest.mark.parametrize(
    "source",
    [
        "x = 1  # Ported from django/db/backends/mysql/base.py::X @ 6.1.3\n",
        "# Ported from django/db/backends/mysql/base.py.\n",
        "#Ported from django/db/backends/mysql/base.py\n",
        "# Ported from django/db/backends/mysql/base.py:42\n",
    ],
    ids=["trailing-comment", "sentence-end", "no-space-after-hash", "line-number"],
)
def test_ported_header_variants_that_name_the_django_file_are_accepted(source):
    # when
    stale = stale_messages({PORTED_PATH: source}, PORTED_LEDGER)
    # then
    assert stale == []


@pytest.mark.parametrize(
    "source",
    [
        "x = 1\n",
        "# Ported from django/db/utils.py::X @ 6.1.3\n",
        "# Ported from django/db/backends/\n# mysql/base.py::X\n",
        "# Ported from django/db/backends/postgresql/base.py::X,\n# django/db/backends/mysql/base.py\n",
        '"""Ported from django/db/backends/mysql/base.py."""\n',
        'x = "# Ported from django/db/backends/mysql/base.py"\n',
        "# ported from django/db/backends/mysql/base.py\n",
        "# Ported from django/db/backends/mysql/base.pyc\n",
        "# Ported from django/db/backends/mysql/base.py-x\n",
    ],
    ids=[
        "no-header",
        "other-module",
        "path-split-over-lines",
        "file-name-only-on-a-continuation-line",
        "docstring",
        "string-literal",
        "lowercase",
        "longer-file-name",
        "hyphenated-file-name",
    ],
)
def test_ported_entry_without_a_matching_ported_from_comment_is_stale(source):
    # when
    stale = stale_messages({PORTED_PATH: source}, PORTED_LEDGER)
    # then
    assert len(stale) == 1
    assert stale[0].startswith("A202")
    assert "django/db/backends/mysql/base.py" in stale[0]


@pytest.mark.parametrize(
    "source",
    ["def (\n# Ported from django/db/backends/mysql/base.py\n", "x = (\n"],
    ids=["unparsable-with-comment", "unterminated-bracket"],
)
def test_ported_check_survives_source_that_does_not_tokenize(source):
    # when
    stale = stale_messages({PORTED_PATH: source}, PORTED_LEDGER)
    # then
    assert len(stale) == 1
    assert stale_codes(stale) == {"A202"}


def test_unparsable_source_is_ignored_by_the_stale_check():
    # when
    stale = stale_messages({"src/asyncopation/query/y.py": "def ("}, EMPTY_LEDGER)
    # then
    assert stale == []


@pytest.mark.parametrize(
    "path",
    ["src/asyncopation/query/_django/x.py", "src/asyncopation/_djangox/x.py"],
    ids=["nested-not-top-level", "prefix-lookalike"],
)
def test_lookalike_django_package_paths_do_not_get_the_exemption(path):
    # when
    codes = lint_src_module(
        "from django.db.models.sql import Query\n", path=path, ledger=SQL_QUERY_LEDGER
    )
    # then
    assert codes == ["A203"]
