"""Driver imports and dynamic-import routes (A101, A103, A104; non-negotiable 2)."""

import pytest

from tests.support.guards import distinct_codes, rule_outcome, with_mysqlclient_ban
from tests.support.reexport_cases import MODULE_PARAMS, NAME_PARAMS

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
DRIVERS = "src/asyncopation/drivers/postgresql/x.py"
BOUNDARY = "src/asyncopation/_django/postgresql_adapters.py"
REEXPORT = "re-exports the psycopg"
ANY = "from django.db.backends.postgresql.psycopg_any import"
BASE = "from django.db.backends.postgresql.base import"
LEAK = ["A104", "A201", "A203"]
INSIDE = ["A201", "A203"]
MYSQL_LEAK = ["A101", "A104", "A201", "A203"]


@pytest.mark.parametrize(("module", "name"), NAME_PARAMS)
def test_every_listed_reexport_is_a_driver_import_outside_drivers(module, name):
    # given
    source = f"from {module} import {name}\n"
    # when
    outcome = rule_outcome(source, QUERY, "A104", REEXPORT)
    # then
    assert outcome == (with_mysqlclient_ban(LEAK, module), True)


@pytest.mark.parametrize("module", MODULE_PARAMS)
def test_a_star_import_of_a_reexporting_module_is_rejected(module):
    # when
    hides = rule_outcome(f"from {module} import *\n", QUERY, "A102", "hides banned names")
    leaks = rule_outcome(f"from {module} import *\n", QUERY, "A104", REEXPORT)
    # then
    assert (hides[1], leaks[1]) == (True, True)


@pytest.mark.parametrize(
    ("source", "path", "codes", "flagged"),
    [
        P(f"{ANY} adapt\n", QUERY, LEAK, True, id="psycopg-any-adapt"),
        P(f"{ANY} sql\n", QUERY, LEAK, True, id="psycopg-any-sql"),
        P(f"{ANY} ClientCursor\n", QUERY, LEAK, True, id="psycopg-any-client-cursor"),
        P(f"{ANY} errors\n", QUERY, LEAK, True, id="psycopg-any-errors"),
        P(f"{ANY} Jsonb\n", QUERY, LEAK, True, id="psycopg-any-jsonb"),
        P(f"{ANY} Range\n", QUERY, LEAK, True, id="psycopg-any-range"),
        P(f"{ANY} pq\n", QUERY, LEAK, True, id="psycopg-any-pq"),
        P("import django.db.backends.postgresql.psycopg_any\n", QUERY, LEAK, True, id="plain-import"),
        P("from django.db.backends.postgresql import psycopg_any\n", QUERY, LEAK, True,
          id="submodule-name"),
        P(f"{BASE} Database\n", QUERY, LEAK, True, id="base-database"),
        P(f"{BASE} sql\n", QUERY, LEAK, True, id="base-sql"),
        P(f"{BASE} adapters\n", QUERY, LEAK, True, id="base-adapters"),
        P(f"{BASE} Format\n", QUERY, LEAK, True, id="base-format"),
        P(f"{BASE} IsolationLevel\n", QUERY, LEAK, True, id="base-isolation-level"),
        P(f"{BASE} psycopg\n", QUERY, LEAK, True, id="base-psycopg"),
        P(f"{BASE} psycopg_pool\n", QUERY, LEAK, True, id="base-psycopg-pool"),
        P("from django.db.backends.mysql.base import Database\n", QUERY, MYSQL_LEAK, True,
          id="mysql-database"),
        P("from django.db.backends.mysql.base import MySQLdb\n", QUERY, MYSQL_LEAK, True,
          id="mysql-mysqldb"),
        P("from django.db.backends.mysql.base import Database\n",
          "src/asyncopation/drivers/mysql/x.py", ["A101", "A201", "A203"], False,
          id="mysql-inside-drivers"),
        P("from django.db.backends.postgresql.operations import Jsonb\n", QUERY, LEAK, True,
          id="operations-jsonb"),
        P("from django.db.backends.postgresql.operations import errors\n", QUERY, LEAK, True,
          id="operations-errors"),
        P("from django.db.backends.postgresql.creation import errors\n", QUERY, LEAK, True,
          id="creation-errors"),
        P("from django.db.backends.postgresql.schema import sql\n", QUERY, LEAK, True,
          id="schema-sql"),
        P("from django.contrib.postgres.search import Dumper\n", QUERY, LEAK, True,
          id="search-dumper"),
        P("from django.contrib.postgres.search import adapt\n", QUERY, LEAK, True,
          id="search-adapt"),
        P("from django.contrib.postgres.fields.ranges import DateRange\n", QUERY, LEAK, True,
          id="ranges-date-range"),
        P("from django.contrib.gis.db.backends.postgis.adapter import sql\n", QUERY, LEAK, True,
          id="postgis-adapter-sql"),
        P(f"{ANY} adapt\n", DRIVERS, INSIDE, False, id="inside-drivers-psycopg-any"),
        P(f"{BASE} Database\n", DRIVERS, INSIDE, False, id="inside-drivers-base"),
        P(f"{ANY} is_psycopg3\n", QUERY, INSIDE, False, id="is-psycopg3-is-allowed"),
        P(f"{BASE} DatabaseWrapper\n", "src/asyncopation/_django/postgresql.py", ["A102", "A201"],
          False, id="wrapper-class-has-its-own-rule"),
    ],
)  # fmt: skip
def test_driver_reexports_are_confined_to_drivers(source, path, codes, flagged):
    # when
    outcome = rule_outcome(source, path, "A104", REEXPORT)
    # then
    assert outcome == (codes, flagged)


@pytest.mark.parametrize(
    ("source", "path", "codes", "rule"),
    [
        P(f"{ANY} get_adapters_template\n", QUERY, LEAK, "may only be imported by", id="outside"),
        P(f"{ANY} register_tzloader\n", QUERY, LEAK, "may only be imported by", id="tzloader-outside"),
        P(f"{ANY} get_adapters_template\n", DRIVERS, LEAK, "may only be imported by", id="in-drivers"),
        P(f"{BASE} register_tzloader\n", QUERY, LEAK, "may only be imported by", id="from-base"),
        P("from django.db.backends.postgresql.operations import mogrify\n", QUERY,
          ["A104", "A201", "A203"], "renders through a cursor", id="mogrify-outside"),
        P("from django.db.backends.postgresql.operations import mogrify\n", DRIVERS,
          ["A104", "A201", "A203"], "renders through a cursor", id="mogrify-in-drivers"),
    ],
)  # fmt: skip
def test_adapter_helpers_and_mogrify_have_their_own_confinement(source, path, codes, rule):
    # when
    outcome = rule_outcome(source, path, "A104", rule)
    # then
    assert outcome == (codes, True)


def test_the_adapter_boundary_module_may_import_the_helpers():
    # given
    source = f"{ANY} get_adapters_template, register_tzloader\n"
    # when
    outcome = rule_outcome(source, BOUNDARY, "A104", "may only be imported by")
    # then
    assert outcome == (["A201"], False)


@pytest.mark.parametrize(
    ("source", "path", "codes", "flagged"),
    [
        P("def f(W):\n    return W.Database.connect('')\n", QUERY, ["A104"], True, id="attribute-call"),
        P("def f(w):\n    return wrapper_for('d', {}, None).Database\n", QUERY, ["A104"],
          True, id="call-result"),
        P("def f(B):\n    return B.Database\n", QUERY, ["A104"], True, id="class-attribute"),
        P("def f(W):\n    return getattr(W, 'Database')\n", QUERY, ["A104"], True, id="constant-getattr"),
        P("def f(W):\n    return W.psycopg\n", QUERY, ["A104"], True, id="psycopg-attribute"),
        P("def f(W):\n    return W.psycopg2\n", QUERY, ["A104"], True, id="psycopg2-attribute"),
        P("def f(W):\n    return W.psycopg_pool\n", QUERY, ["A104"], True, id="psycopg-pool-any"),
        P("def f(W):\n    return W.MySQLdb\n", QUERY, ["A104"], True, id="mysqldb-any-object"),
        P("def f(W):\n    return W.Database\n", DRIVERS, [], False, id="inside-drivers"),
        P("import django.db.backends.postgresql.base as b\n\ndef f():\n    return b.psycopg.connect('')\n",
          QUERY, LEAK, True, id="module-alias-psycopg"),
        P("from django.db.backends.postgresql import base\n\ndef f():\n    return base.psycopg\n",
          QUERY, LEAK, True, id="submodule-name-psycopg"),
        P("import django.db.backends.postgresql.base as b\n\ndef f():\n    return getattr(b, 'psycopg2')\n",
          QUERY, LEAK, True, id="constant-getattr-psycopg2"),
        P("import django.db.backends.postgresql.base as b\n\ndef f():\n    return b.psycopg_pool\n",
          QUERY, LEAK, True, id="psycopg-pool-attribute"),
        P("import django.db.backends.mysql.base as b\n\ndef f():\n    return b.MySQLdb\n",
          QUERY, MYSQL_LEAK, True, id="mysqldb-attribute"),
        P("import django.db.backends.postgresql.base as b\n\ndef f():\n    return b.psycopg\n",
          DRIVERS, ["A201", "A203"], False, id="psycopg-attribute-inside-drivers"),
    ],
)  # fmt: skip
def test_the_database_attribute_is_the_psycopg_module_outside_drivers(source, path, codes, flagged):
    # when
    outcome = rule_outcome(source, path, "A104", "psycopg module itself")
    # then
    assert outcome == (codes, flagged)


@pytest.mark.parametrize(
    "source",
    [
        P("def f(x):\n    return hasattr(x, 'Database')\n", id="hasattr"),
        P("def f(x):\n    return x.database\n", id="lower-case-attribute"),
        P("def f(x):\n    return getattr(x, 'Databases')\n", id="other-constant-name"),
        P(
            "from asyncopation.query.x import Database\n",
            id="import-of-the-name-from-our-own-module",
        ),
    ],
)
def test_names_that_only_look_like_the_driver_attributes_are_left_alone(source):
    # when
    codes = distinct_codes(source, QUERY)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("module", "name"),
    [
        P("django.db.backends.postgresql.base", "sql", id="base-sql"),
        P("django.db.backends.postgresql.base", "Database", id="base-database"),
        P("django.db.backends.postgresql.operations", "errors", id="operations-errors"),
        P("django.db.backends.mysql.base", "CLIENT", id="mysql-client"),
        P("django.db.backends.mysql.base", "FIELD_TYPE", id="mysql-field-type"),
        P("django.db.backends.mysql.base", "conversions", id="mysql-conversions"),
        P("django.db.backends.mysql.introspection", "FIELD_TYPE", id="mysql-introspection"),
        P("django.contrib.gis.db.backends.mysql.introspection", "FIELD_TYPE", id="gis-mysql"),
        P("django.contrib.postgres.search", "adapt", id="search-adapt"),
    ],
)
def test_literal_reexports_stay_pinned_even_if_the_table_loses_a_row(module, name):
    # given
    source = f"from {module} import {name}\n"
    # when
    outcome = rule_outcome(source, QUERY, "A104", REEXPORT)
    # then
    assert outcome == (with_mysqlclient_ban(LEAK, module), True)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        P("from importlib.metadata import version\n", [], id="metadata-from-import"),
        P("from importlib import metadata\n", [], id="metadata-name"),
        P("from importlib import resources\n", [], id="resources-name"),
        P("import importlib.metadata as md\n", [], id="metadata-aliased"),
        P("import importlib\n", ["A101"], id="import-importlib"),
        P("import importlib.metadata\n", ["A101"], id="metadata-binds-importlib"),
        P("import importlib.resources\n", ["A101"], id="resources-binds-importlib"),
        P("from importlib import import_module\n", ["A101"], id="import-module"),
        P("import importlib.util\n", ["A101"], id="util"),
        P("from importlib.util import find_spec\n", ["A101"], id="util-from"),
        P("import importlib.machinery\n", ["A101"], id="machinery"),
        P("import importlib.abc\n", ["A101"], id="abc"),
        P("import pkgutil\n", ["A101"], id="pkgutil"),
        P("import runpy\n", ["A101"], id="runpy"),
        P("import pydoc\n", ["A101"], id="pydoc"),
        P("import builtins\n", ["A101"], id="builtins"),
        P("from django.utils.module_loading import import_string\n", ["A101", "A201", "A203"], id="import-string"),
        P("import django.utils.module_loading as ml\n", ["A101", "A201", "A203"], id="module-loading-aliased"),
        P("from django.utils import module_loading\n", ["A101", "A201", "A203"], id="module-loading-from-package"),
        P("from django.utils import timezone\n", ["A201", "A203"], id="sibling-module-is-fine"),
        P("from asgiref import sync\n", ["A101"], id="banned-module-from-package"),
        P("x = __builtins__\n", ["A103"], id="builtins-name"),
        P("__import__('x')\n", ["A103"], id="dunder-import"),
        P("eval('1')\n", ["A103"], id="eval"),
        P("exec('1')\n", ["A103"], id="exec"),
        P("compile('1', 'x', 'eval')\n", ["A103"], id="compile"),
        P("def f(g):\n    return g.__globals__\n", ["A103"], id="globals-attribute"),
        P("def f(g):\n    return g.__closure__\n", ["A103"], id="closure-attribute"),
        P("def f(g):\n    return g.__wrapped__\n", ["A103"], id="wrapped-attribute"),
        P("def f(g):\n    return g.__self__\n", ["A103"], id="self-attribute"),
        P("def f(g):\n    return g.__import__\n", ["A103"], id="import-attribute"),
        P("def f(g):\n    return g.__builtins__\n", ["A103"], id="builtins-attribute"),
    ],
)  # fmt: skip
def test_dynamic_import_routes_are_banned_without_false_positives(source, expected):
    # when
    codes = distinct_codes(source, QUERY)
    # then
    assert codes == expected


def test_psycopg_version_is_a_django_function_and_not_a_driver_name():
    # given
    source = "from django.db.backends.postgresql.base import psycopg_version\n"
    # when
    outcome = rule_outcome(source, QUERY, "A104", "")
    # then
    assert outcome == (["A201", "A203"], False)
