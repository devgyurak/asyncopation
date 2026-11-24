import pytest

from tests.support.guards import EMPTY_LEDGER, lint_src_module
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
        ("from django.db.models.query import ModelIterable\n", ["A201"]),
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
        "unledgered-internal",
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
    codes = lint_src_module("from django.db.models.query import ModelIterable\n", ledger=ledger)
    # then
    assert codes == []
