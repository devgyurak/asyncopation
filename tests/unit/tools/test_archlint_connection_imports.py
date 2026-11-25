"""Who may import what around the connection boundary (A102; ADR-0008, Decision 6)."""

import pytest

from tests.support.guards import distinct_codes, rule_outcome

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
ENGINE = "src/asyncopation/engine/x.py"
SCOPE = "src/asyncopation/compile/scope.py"
COMPILE = "src/asyncopation/compile/postgresql.py"
BOUNDARY = "from asyncopation._django.connections import"
IMPORTABLE = "may only be imported by compile/scope.py"
REACHABLE = "is reachable only from compile/scope.py"
PRIVATE = "is private to compile/scope.py"
PACKAGE = "do not bind the `asyncopation._django` package object"
REBOUND = "reaching `asyncopation._django` through a bound"
WRAPPER_ALIAS = "may only be imported as `BaseWrapper` by the `_django` backend modules"
MACHINERY = "must not be used"
REACHED = "reached through a Django import"


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P("from django.db import connections\n", MACHINERY, id="from-import"),
        P("from django.db import connection\n", MACHINERY, id="connection"),
        P("from django.db import transaction\n", MACHINERY, id="transaction"),
        P("from django.db import close_old_connections\n", MACHINERY, id="close-old-connections"),
        P("from django.db import reset_queries\n", MACHINERY, id="reset-queries"),
        P("from django.db import ConnectionHandler\n", MACHINERY, id="connection-handler"),
        P("from django.db import ConnectionRouter\n", MACHINERY, id="connection-router"),
        P("from django.db import load_backend\n", MACHINERY, id="load-backend"),
        P("from django.db import router\n", MACHINERY, id="router"),
        P("from django.db.models.query import connections\n", MACHINERY, id="reexport-import"),
        P("import django.db as d\n", "hides `db.connections`", id="aliased-module"),
        P("from django import db\n", "hides `db.connections`", id="from-django-import-db"),
        P("from django.db import *\n", "hides banned names", id="star-import-db"),
        P("from django import *\n", "hides banned names", id="star-import-django"),
        P("import django.db\n\ndef f(a):\n    return django.db.connections[a]\n", REACHED, id="dotted"),
        P("import django.db\n\ndef f():\n    return django.db.transaction.atomic\n", REACHED,
          id="dotted-transaction"),
        P("import django.db\n\ndef f():\n    return django.db.ConnectionHandler\n", REACHED,
          id="dotted-handler"),
        P("import django.db.models\n\ndef f(a):\n    return django.db.models.base.connections[a]\n",
          REACHED, id="reexport-dotted"),
        P("import django.db.models as m\n\ndef f():\n    return m.query.connections\n", REACHED,
          id="reexport-through-alias"),
        P("from django.db import models\n\ndef f():\n    return getattr(models, 'connections')\n",
          "reaches Django's connection machinery", id="reexport-getattr"),
    ],
)  # fmt: skip
def test_django_connection_machinery_is_reported_anywhere_in_src(source, rule):
    # when
    outcome = rule_outcome(source, QUERY, "A102", rule)
    # then
    assert outcome == (["A102"], True)


def test_the_base_connection_handler_is_machinery_too():
    # given
    source = "from django.utils.connection import BaseConnectionHandler\n"
    # when
    outcome = rule_outcome(source, QUERY, "A102", MACHINERY)
    # then
    assert outcome == (["A102", "A201", "A203"], True)


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(f"{BOUNDARY} swapped\n", IMPORTABLE, id="name-import"),
        P("from asyncopation._django import connections\n", REACHABLE, id="module-import"),
        P("import asyncopation._django.connections\n", IMPORTABLE, id="plain-import"),
        P("import asyncopation._django.connections as c\n", IMPORTABLE, id="aliased-import"),
        P(f"{BOUNDARY} *\n", IMPORTABLE, id="star-import"),
        P("from .._django.connections import swapped\n", IMPORTABLE, id="relative-name"),
        P("from .._django import connections\n", REACHABLE, id="relative-module"),
        P("from asyncopation import _django\n_django.connections.swapped('d', None)\n", PACKAGE,
          id="package-attribute"),
        P("from asyncopation import _django as d\n", PACKAGE, id="package-alias"),
        P("import asyncopation._django as d\n", PACKAGE, id="package-dotted-alias"),
        P("from asyncopation._django import *\n", REACHABLE, id="package-star"),
        P("import asyncopation._django.postgresql\nasyncopation._django.connections.swapped\n",
          REBOUND, id="dotted-attribute-chain"),
        P("import asyncopation\n\ndef f(a, w):\n    asyncopation._django.connections.swapped(a, w)\n",
          PRIVATE, id="dotted-call"),
        P("import asyncopation\n\ndef f():\n    return asyncopation._django.connections\n",
          REACHABLE, id="dotted-bare-module"),
        P("import asyncopation\n\ndef f():\n"
          "    return asyncopation._django.connections.ScopeContextError\n", REACHABLE,
          id="dotted-error"),
        P("import asyncopation\n\nx = asyncopation._django\n", REBOUND, id="package-rebound"),
        P("import asyncopation as a\n\ndef f():\n    return a._django.connections\n", REBOUND,
          id="package-rebound-through-alias"),
        P("import asyncopation as a\n\ndef f(n):\n    return getattr(a, n)\n",
          "computed name on an `asyncopation` object", id="computed-getattr"),
    ],
)  # fmt: skip
def test_the_connections_boundary_is_not_importable_or_reachable_outside_the_scope_module(
    source, rule
):
    # when
    outcome = rule_outcome(source, ENGINE, "A102", rule)
    # then
    assert outcome == (["A102"], True)


def test_the_dotted_path_inside_the_django_package_trips_only_the_reachability_rule():
    # given
    source = "import asyncopation\n\ndef f():\n    return asyncopation._django.connections\n"
    # when
    outcome = rule_outcome(source, "src/asyncopation/_django/x.py", "A102", REACHABLE)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    "source",
    [
        P(f"{BOUNDARY} swapped as _swapped\n", id="swapped"),
        P(f"{BOUNDARY} ScopeContextError\n", id="error"),
        P(f"{BOUNDARY} ScopeContextError, swapped as _swapped\n", id="both"),
    ],
)
def test_the_scope_module_may_import_the_error_and_swapped(source):
    # when
    codes = distinct_codes(source, SCOPE)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(f"{BOUNDARY} swapped, other\n", IMPORTABLE, id="extra-name"),
        P(f"{BOUNDARY} ScopeContextError, other\n", IMPORTABLE, id="error-and-extra"),
        P(f"{BOUNDARY} *\n", IMPORTABLE, id="star"),
        P("import asyncopation._django.connections as c\n", IMPORTABLE, id="aliased-module"),
        P("from asyncopation._django import connections\n", REACHABLE, id="module-import"),
    ],
)
def test_the_scope_module_may_import_nothing_else_from_the_boundary(source, rule):
    # when
    outcome = rule_outcome(source, SCOPE, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path", "codes"),
    [
        P("from .connections import pool\n", "src/asyncopation/connection/x.py", [], id="other-package"),
        P("from .connections import swapped\n", "src/asyncopation/_django/x.py", ["A102"],
          id="relative-inside-the-django-package"),
    ],
)  # fmt: skip
def test_a_relative_connections_import_is_judged_by_what_it_resolves_to(source, path, codes):
    # when
    resolved = distinct_codes(source, path)
    # then
    assert resolved == codes


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P("from asyncopation.compile.scope import _swapped\n", PRIVATE, id="import"),
        P("def f(s):\n    return s._swapped\n", "`._swapped` is private", id="attribute"),
        P("def f(s):\n    return getattr(s, '_swapped')\n", "reaches the private swap",
          id="getattr"),
        P("def f(s):\n    return s.swapped\n", "`.swapped` is private", id="public-name-attribute"),
        P("def f(s):\n    return getattr(s, 'swapped')\n", "reaches the private swap",
          id="public-name-getattr"),
    ],
)  # fmt: skip
def test_the_swap_name_is_reported_outside_the_scope_module(source, rule):
    # when
    outcome = rule_outcome(source, QUERY, "A102", rule)
    # then
    assert outcome == (["A102"], True)


def test_a_relative_import_of_the_private_swap_name_is_reported():
    # when
    outcome = rule_outcome(
        "from .scope import _swapped\n", "src/asyncopation/compile/x.py", "A102", PRIVATE
    )
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path", "codes", "rule"),
    [
        P("from asyncopation._django.postgresql import BaseWrapper\n", QUERY, ["A102"],
          "wrapper class may only be imported by", id="query"),
        P("from asyncopation.compile.postgresql import BaseWrapper\n", QUERY, ["A102"],
          "wrapper class may only be imported by", id="re-exported-through-compile"),
        P("from asyncopation._django.postgresql import BaseWrapper\n",
          "src/asyncopation/query/compile/x.py", ["A102"], "wrapper class may only be imported by",
          id="lookalike-compile-directory"),
        P("from asyncopation._django.postgresql import BaseWrapper\n", "src/other/compile/x.py",
          ["A102"], "wrapper class may only be imported by", id="foreign-compile-directory"),
        P("from asyncopation._django.postgresql import BaseWrapper\n", "src/other/_django/x.py",
          ["A102"], "wrapper class may only be imported by", id="foreign-django-directory"),
        P("from other import BaseWrapper\n", QUERY, ["A102"], "wrapper class may only be imported by",
          id="any-module"),
        P("from asyncopation._django.postgresql import *\n", QUERY, ["A102"],
          "wrapper class may only be imported by", id="star-import"),
        P("from asyncopation._django import postgresql as p\n\ndef f():\n    return p.BaseWrapper\n",
          QUERY, ["A102"], "is for compile/ only", id="attribute-of-the-module-alias"),
        P("import asyncopation\n\ndef f():\n    return asyncopation._django.postgresql.BaseWrapper\n",
          QUERY, ["A102"], "is for compile/ only", id="dotted"),
        P("from asyncopation._django import postgresql as pg\n\ndef f():\n"
          "    return getattr(pg, 'BaseWrapper')\n", QUERY, ["A102"], "is for compile/ only",
          id="getattr"),
    ],
)  # fmt: skip
def test_the_real_postgresql_wrapper_class_is_confined_to_compile_and_the_django_package(
    source, path, codes, rule
):
    # when
    outcome = rule_outcome(source, path, "A102", rule)
    # then
    assert outcome == (codes, True)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P("from asyncopation._django.postgresql import BaseWrapper\n", COMPILE, id="compile"),
        P("from asyncopation._django.postgresql import BaseWrapper\n",
          "src/asyncopation/compile/x.py", id="compile-module"),
        P("from asyncopation.compile.postgresql import BaseWrapper\n",
          "src/asyncopation/_django/x.py", id="django-package"),
        P("import asyncopation._django.postgresql\n", COMPILE, id="plain-import-in-compile"),
        P("from asyncopation._django import postgresql\n", QUERY, id="module-import-is-fine"),
        P("import asyncopation\n\nx = asyncopation._django\n", "src/asyncopation/_django/x.py",
          id="package-rebound-inside-the-django-package"),
    ],
)  # fmt: skip
def test_the_wrapper_class_rules_leave_legitimate_uses_alone(source, path):
    # when
    codes = distinct_codes(source, path)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "path", "codes", "flagged"),
    [
        P("from django.db.backends.postgresql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/postgresql.py", ["A201"], False, id="allowed-in-postgresql"),
        P("from django.db.backends.mysql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/mysql.py", ["A201"], False, id="allowed-in-mysql"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper as RealWrapper\n",
          "src/asyncopation/_django/foo.py", ["A102", "A201"], True, id="other-django-module"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/foo.py", ["A102", "A201"], True, id="right-alias-wrong-path"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper as W\n",
          "src/asyncopation/_django/postgresql.py", ["A102", "A201"], True, id="wrong-alias"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper\n", QUERY,
          ["A102", "A201", "A203"], True, id="outside-the-django-package"),
    ],
)  # fmt: skip
def test_djangos_real_database_wrapper_is_imported_only_as_base_wrapper(
    source, path, codes, flagged
):
    # when
    outcome = rule_outcome(source, path, "A102", WRAPPER_ALIAS)
    # then
    assert outcome == (codes, flagged)


def test_importing_the_django_transaction_module_is_a_banned_module():
    # when
    outcome = rule_outcome("import django.db.transaction\n", QUERY, "A101", "django.db.transaction")
    # then
    assert outcome == (["A101", "A201", "A203"], True)


def test_a_wrapper_attribute_reached_through_a_django_bound_name_is_reported():
    # given
    source = (
        "import django.db.backends.postgresql.base as b\n\nclass X(b.DatabaseWrapper):\n    pass\n"
    )
    # when
    outcome = rule_outcome(source, QUERY, "A102", WRAPPER_ALIAS)
    # then
    assert outcome == (["A102", "A201", "A203"], True)


@pytest.mark.parametrize(
    "source",
    [
        P("class Thing:\n    pass\n\nmine = Thing()\n\ndef f():\n    return mine.DatabaseWrapper\n",
          id="locally-defined-object"),
        P("from asyncopation.compile import postgresql as pg\n\ndef f():\n    return pg.DatabaseWrapper\n",
          id="asyncopation-side-name"),
    ],
)  # fmt: skip
def test_a_wrapper_attribute_on_a_non_django_name_is_not_reported(source):
    # when
    codes = distinct_codes(source, QUERY)
    # then
    assert codes == []
