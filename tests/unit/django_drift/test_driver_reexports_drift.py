"""tools/guards/reexports.py lists every psycopg name the installed Django's modules bind."""

import pytest

from tests.support.psycopg_names import (
    MODULE_PARAMS,
    MODULES,
    bound_driver_names,
    table_without,
    unlisted_driver_names,
)
from tools.guards.reexports import DRIVER_REEXPORTS

BASE = "django.db.backends.postgresql.base"


@pytest.mark.parametrize("module", MODULE_PARAMS)
def test_every_driver_name_a_django_module_binds_is_in_the_guard_table(module):
    # when
    unlisted = unlisted_driver_names(module)
    # then
    assert unlisted == []


def test_the_drift_check_notices_a_name_removed_from_the_table():
    # given
    table = {**DRIVER_REEXPORTS, BASE: DRIVER_REEXPORTS[BASE] - {"Database"}}
    # when
    unlisted = unlisted_driver_names(BASE, table)
    # then
    assert unlisted == ["Database"]


def test_the_drift_check_notices_a_module_missing_from_the_table():
    # given
    table = table_without(BASE)
    # when
    unlisted = unlisted_driver_names(BASE, table)
    # then
    assert unlisted == [
        "ConnectionPool",
        "Database",
        "Format",
        "IsolationLevel",
        "adapters",
        "psycopg2",
        "sql",
    ]


@pytest.mark.parametrize(
    ("source", "names"),
    [
        pytest.param("from psycopg import sql\n", {"sql"}, id="from-import"),
        pytest.param("import psycopg as Database\n", {"Database"}, id="import-as"),
        pytest.param("import psycopg.pq\n", {"psycopg"}, id="dotted-import"),
        pytest.param("from .psycopg_any import adapt\n", {"adapt"}, id="relative-psycopg-any"),
        pytest.param(
            "from django.db.backends.postgresql.psycopg_any import Jsonb as J\n",
            {"J"},
            id="absolute-psycopg-any",
        ),
        pytest.param(
            "def f():\n    from psycopg_pool import ConnectionPool\n",
            {"ConnectionPool"},
            id="in-function",
        ),
        pytest.param("import os\nfrom json import loads\n", set(), id="unrelated"),
    ],
)
def test_the_scanner_finds_driver_names_in_every_import_form(source, names):
    # when
    found = bound_driver_names(source)
    # then
    assert found == names


PACKAGE = "django.db.backends.postgresql.x"
POSTGIS_BASE = "django.contrib.gis.db.backends.postgis.base"


@pytest.mark.parametrize(
    ("source", "module", "names"),
    [
        pytest.param("from .base import Database, sql\n", PACKAGE, {"Database", "sql"},
                     id="relative-re-export-of-a-listed-module"),
        pytest.param("from django.db.backends.postgresql.base import Database as D\n", "", {"D"},
                     id="absolute-re-export-aliased"),
        pytest.param("from django.db.backends.postgresql.base import DatabaseWrapper\n", "", set(),
                     id="unlisted-name-of-a-listed-module"),
        pytest.param("from .base import *\n", PACKAGE, {"*"}, id="star-of-a-listed-module"),
        pytest.param("from .features import DatabaseFeatures\n", PACKAGE, set(),
                     id="module-not-in-the-table"),
        pytest.param("from django.db.backends.postgresql import psycopg_any\n", "",
                     {"psycopg_any"}, id="module-object-by-name"),
        pytest.param("from . import psycopg_any as pa\n", PACKAGE, {"pa"},
                     id="relative-module-object-aliased"),
        pytest.param("import django.db.backends.postgresql.psycopg_any as pa\n", "", {"pa"},
                     id="module-object-by-import-as"),
        pytest.param("from django.db.backends.postgresql import base\n", "", set(),
                     id="submodule-object-is-not-a-driver-name"),
        pytest.param("import MySQLdb as Database\n", "", {"Database"}, id="mysqldb-import"),
        pytest.param("from MySQLdb.constants import CLIENT\n", "", {"CLIENT"},
                     id="mysqldb-from-import"),
    ],
)  # fmt: skip
def test_the_scanner_follows_reexports_of_listed_modules_and_module_objects(source, module, names):
    # when
    found = bound_driver_names(source, module)
    # then
    assert found == names


def test_every_module_of_the_table_is_scanned_by_the_drift_check():
    # when
    unscanned = set(DRIVER_REEXPORTS) - set(MODULES)
    # then
    assert unscanned == set()


def test_a_name_taken_from_a_listed_module_is_a_reexport_too():
    # given: postgis imports `DatabaseWrapper` from the listed postgresql `base` module
    table = {
        **DRIVER_REEXPORTS,
        BASE: frozenset({"DatabaseWrapper"}),
        POSTGIS_BASE: DRIVER_REEXPORTS[POSTGIS_BASE],
    }
    # when
    unlisted = unlisted_driver_names(POSTGIS_BASE, table)
    # then
    assert unlisted == ["PsycopgDatabaseWrapper"]
