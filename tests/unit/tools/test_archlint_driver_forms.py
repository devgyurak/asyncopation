"""One verdict per (Django module, name), whichever way the name is reached (A104, G1)."""

import pytest

from tests.support.guards import driver_form_verdicts, has_violation, same_verdict

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
DRIVERS = "src/asyncopation/drivers/postgresql/x.py"
BOUNDARY = "src/asyncopation/_django/postgresql_adapters.py"
BASE = "django.db.backends.postgresql.base"
OPS = "django.db.backends.postgresql.operations"
ANY = "django.db.backends.postgresql.psycopg_any"
PACKAGE = "django.db.backends.postgresql"


@pytest.mark.parametrize(
    ("module", "name", "path", "expected"),
    [
        P(BASE, "sql", QUERY, same_verdict(True), id="base-sql"),
        P(BASE, "adapters", QUERY, same_verdict(True), id="base-adapters"),
        P(BASE, "Format", QUERY, same_verdict(True), id="base-format"),
        P(BASE, "IsolationLevel", QUERY, same_verdict(True), id="base-isolation-level"),
        P(BASE, "Database", QUERY, same_verdict(True), id="base-database"),
        P(BASE, "psycopg", QUERY, same_verdict(True), id="base-psycopg"),
        P(BASE, "sql", DRIVERS, same_verdict(False), id="base-sql-in-drivers"),
        P(BASE, "DatabaseWrapper", QUERY, same_verdict(False), id="base-wrapper-has-its-own-rule"),
        P(OPS, "errors", QUERY, same_verdict(True), id="operations-errors"),
        P(OPS, "Jsonb", QUERY, same_verdict(True), id="operations-jsonb"),
        P(OPS, "mogrify", QUERY, same_verdict(True), id="operations-mogrify"),
        P(OPS, "mogrify", DRIVERS, same_verdict(True), id="operations-mogrify-in-drivers"),
        P(OPS, "DatabaseOperations", QUERY, same_verdict(False), id="operations-class"),
        P(PACKAGE, "psycopg_any", QUERY, same_verdict(True), id="package-psycopg-any"),
        P(PACKAGE, "psycopg_any", DRIVERS, same_verdict(False), id="package-psycopg-any-in-drivers"),
        P(ANY, "ClientCursor", QUERY, same_verdict(True), id="psycopg-any-client-cursor"),
        P(ANY, "adapt", QUERY, same_verdict(True), id="psycopg-any-adapt"),
        P(ANY, "get_adapters_template", QUERY, same_verdict(True), id="helper-outside-the-boundary"),
        P(ANY, "get_adapters_template", DRIVERS, same_verdict(True), id="helper-in-drivers"),
        P(ANY, "ClientCursor", DRIVERS, same_verdict(False), id="psycopg-any-in-drivers"),
        P(BASE, "get_adapters_template", BOUNDARY, same_verdict(False), id="helper-in-the-boundary"),
        P(BASE, "register_tzloader", QUERY, same_verdict(True), id="base-helper-outside"),
        P("django.db.backends.mysql.base", "Database", QUERY, same_verdict(True), id="mysql-database"),
        P("django.db.backends.mysql.base", "CLIENT", QUERY, same_verdict(True), id="mysql-client"),
        P("django.contrib.gis.db.backends.postgis.base", "TypeInfo", QUERY, same_verdict(True),
          id="postgis-type-info"),
        P("django.db.backends.postgresql.features", "DatabaseFeatures", QUERY, same_verdict(False),
          id="unrelated-name"),
    ],
)  # fmt: skip
def test_import_attribute_and_getattr_reach_the_same_verdict(module, name, path, expected):
    # when
    verdicts = driver_form_verdicts(module, name, path)
    # then
    assert verdicts == expected


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P("import django.db.backends.postgresql as pg\n\ndef f():\n    return pg.psycopg_any.ClientCursor\n",
          QUERY, id="chain-through-the-package"),
        P("import django.db.backends.postgresql.base\n\ndef f():\n    return django.db.backends.postgresql.base.sql\n",
          QUERY, id="dotted-chain"),
        P("from django.db.backends.postgresql import operations\n\ndef f(a):\n    return operations.mogrify(a)\n",
          DRIVERS, id="mogrify-through-a-module-name"),
        P("def f(cursor):\n    return cursor.mogrify('x')\n", QUERY, id="mogrify-on-any-object"),
        P("import django.db.backends.postgresql as pg\n\ndef f():\n    return getattr(pg, 'operations').mogrify\n",
          DRIVERS, id="mogrify-through-a-constant-getattr-of-a-submodule"),
        P("from django.db.backends import postgresql as pg\n\ndef f():\n    return getattr(pg, 'base').sql\n",
          QUERY, id="attribute-of-a-getattr-submodule"),
        P("from django.db.backends import postgresql as pg\n\ndef f():\n    return getattr(pg, 'base').sql\n",
          "src/asyncopation/_django/x.py", id="attribute-of-a-getattr-submodule-in-django"),
    ],
)  # fmt: skip
def test_longer_routes_to_the_driver_are_reported(source, path):
    # when
    flagged = has_violation(source, path, "A104", "")
    # then
    assert flagged is True


def test_a_driver_cursor_may_mogrify_inside_drivers_only():
    # given
    source = "def f(cursor):\n    return cursor.mogrify('x')\n"
    # when
    verdicts = (
        has_violation(source, DRIVERS, "A104", ""),
        has_violation(source, QUERY, "A104", "driver cursor method"),
    )
    # then
    assert verdicts == (False, True)


@pytest.mark.parametrize(
    "source",
    [
        P("import django.db.backends.postgresql as pg\n\ndef f(name):\n    return getattr(pg, name).sql\n",
          id="variable-getattr-name"),
        P("import django.db.backends.postgresql as pg\n\ndef f(g):\n    return g(pg, 'base').sql\n",
          id="another-function"),
        P("import django.db.backends.postgresql.base as b\n\ndef f():\n    return b.DatabaseWrapper.sql\n",
          id="class-attribute-is-not-a-module"),
        P("from some.module import mogrify\n", id="mogrify-imported-from-a-non-django-module"),
        P("from djangoish.x import mogrify\n", id="mogrify-from-a-module-that-only-looks-like-django"),
        P("import django.db.backends.postgresql as pg\n\ndef f():\n    return getattr(pg)\n",
          id="one-argument-getattr"),
        P("import django.db.backends.postgresql as pg\n\ndef f():\n    return getattr(pg, b'base').sql\n",
          id="non-string-getattr-name"),
    ],
)  # fmt: skip
def test_chains_that_do_not_reach_a_driver_name_are_not_reported(source):
    # when
    flagged = has_violation(source, QUERY, "A104", "")
    # then
    assert flagged is False
