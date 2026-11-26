"""The MySQL backend boundary modules and the compile-only wrapper (A101, A102, A104; ADR-0008)."""

import pytest

from tests.support.guards import distinct_codes, real_module_codes, rule_outcome

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
ENGINE = "src/asyncopation/engine/x.py"
COMPILE = "src/asyncopation/compile/x.py"
COMPILE_MYSQL = "src/asyncopation/compile/mysql.py"
DJANGO = "src/asyncopation/_django/other.py"
DJANGO_MYSQL = "src/asyncopation/_django/mysql.py"
MY_WRAPPER = "from asyncopation._django.mysql import BaseWrapper"
MIXIN = "from asyncopation.compile.wrapper import CompileOnlyMixin"
ONLY_COMPILE = "Django's real PostgreSQL wrapper class may only be imported by compile/"
BASE_MYSQL = "from django.db.backends.mysql.base import"


@pytest.mark.parametrize(
    "path",
    [
        "src/asyncopation/compile/mysql.py",
        "src/asyncopation/_django/mysql.py",
        "src/asyncopation/compile/server_info.py",
        "src/asyncopation/compile/wrapper.py",
        "src/asyncopation/compile/postgresql.py",
    ],
)
def test_the_repositorys_own_backend_modules_pass_every_guard(path):
    # when
    codes = real_module_codes(path)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{MY_WRAPPER}\n{MIXIN}\n\nclass X(CompileOnlyMixin, BaseWrapper):\n    pass\n",
          COMPILE_MYSQL, id="mixin-first-in-compile-mysql"),
        P(f"{MY_WRAPPER}\n{MIXIN}\n\nclass X(CompileOnlyMixin, BaseWrapper):\n    pass\n",
          COMPILE, id="mixin-first-in-compile"),
        P(f"{MY_WRAPPER}\n\ndef f(w):\n    return isinstance(w, BaseWrapper)\n", COMPILE_MYSQL,
          id="isinstance-in-compile-mysql"),
        P("from asyncopation._django import mysql as m\n\ndef f(w):\n    return isinstance(w, m.BaseWrapper)\n",
          COMPILE_MYSQL, id="attribute-of-the-module-in-compile"),
        P("from asyncopation._django import mysql\n", ENGINE, id="module-import-is-fine"),
    ],
)  # fmt: skip
def test_the_mysql_boundary_wrapper_alias_is_allowed_in_compile(source, path):
    # when
    codes = distinct_codes(source, path)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{MY_WRAPPER}\n", QUERY, id="query"),
        P(f"{MY_WRAPPER}\n", ENGINE, id="engine"),
        P(f"{MY_WRAPPER}\n\nclass X(BaseWrapper):\n    pass\n", ENGINE, id="engine-subclass"),
        P("from asyncopation._django.mysql import *\nfrom asyncopation._django.mysql import BaseWrapper\n",
          ENGINE, id="engine-star-and-name"),
        P("from asyncopation.compile.mysql import BaseWrapper\n", ENGINE, id="engine-re-export"),
        P("from asyncopation.compile.mysql import BaseWrapper\n", QUERY, id="query-re-export"),
    ],
)  # fmt: skip
def test_the_mysql_boundary_wrapper_alias_is_rejected_outside_compile_and_the_boundary(
    source, path
):
    # when
    outcome = rule_outcome(source, path, "A102", ONLY_COMPILE)
    # then
    assert outcome[1] is True


def test_attribute_use_of_the_mysql_wrapper_alias_is_rejected_in_the_engine():
    # given
    source = (
        "from asyncopation._django import mysql as m\n\n"
        "def f(w):\n    return isinstance(w, m.BaseWrapper)\n"
    )
    # when
    outcome = rule_outcome(source, ENGINE, "A102", "is for compile/ only")
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path", "fragment"),
    [
        P(f"{MY_WRAPPER} as RealMySQL\n", COMPILE_MYSQL, "do not rename `BaseWrapper`",
          id="renamed"),
        P(f"{MY_WRAPPER}\nW = BaseWrapper\n", COMPILE_MYSQL, "do not rebind `BaseWrapper`",
          id="rebound"),
        P(f"{MY_WRAPPER}\nW = {{'a': BaseWrapper}}\n", COMPILE_MYSQL,
          "do not rebind `BaseWrapper`", id="rebound-in-a-container"),
        P(f"{MY_WRAPPER}\n\nclass X(BaseWrapper):\n    pass\n", COMPILE_MYSQL,
          "must list `CompileOnlyMixin`", id="no-mixin"),
        P(f"{MY_WRAPPER}\n{MIXIN}\n\nclass X(BaseWrapper, CompileOnlyMixin):\n    pass\n",
          COMPILE_MYSQL, "must list `CompileOnlyMixin`", id="mixin-last"),
        P(f"{MY_WRAPPER}\n\nSHARED = BaseWrapper({{}}, 'default')\n", COMPILE_MYSQL,
          "do not instantiate", id="instantiated"),
        P("from asyncopation._django import mysql as m\n\nclass X(m.BaseWrapper):\n    pass\n",
          DJANGO, "must list `CompileOnlyMixin`", id="attribute-base-no-mixin"),
        P("from asyncopation._django.postgresql import BaseWrapper as RealPG\n", COMPILE_MYSQL,
          "do not rename `BaseWrapper`", id="postgresql-alias-renamed-in-the-mysql-module"),
    ],
)  # fmt: skip
def test_the_mysql_wrapper_alias_follows_the_same_rules_as_the_postgresql_one(
    source, path, fragment
):
    # when
    outcome = rule_outcome(source, path, "A102", fragment)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{BASE_MYSQL} DatabaseWrapper as BaseWrapper\n", COMPILE_MYSQL, id="compile-mysql"),
        P(f"{BASE_MYSQL} DatabaseWrapper as BaseWrapper\n", COMPILE, id="compile"),
        P(f"{BASE_MYSQL} DatabaseWrapper\n", DJANGO_MYSQL, id="plain-name-in-the-boundary"),
        P(f"{BASE_MYSQL} DatabaseWrapper as RealWrapper\n", DJANGO_MYSQL,
          id="wrong-alias-in-the-boundary"),
        P(f"{BASE_MYSQL} DatabaseWrapper as BaseWrapper\n", DJANGO, id="other-django-module"),
    ],
)  # fmt: skip
def test_djangos_real_mysql_wrapper_is_imported_only_by_the_mysql_boundary_as_base_wrapper(
    source, path
):
    # when
    outcome = rule_outcome(
        source, path, "A102", "may only be imported as `BaseWrapper` by the `_django` backend"
    )
    # then
    assert outcome[1] is True


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{BASE_MYSQL} Database\n", COMPILE_MYSQL, id="database-in-compile-mysql"),
        P(f"{BASE_MYSQL} Database\n", DJANGO_MYSQL, id="database-in-the-boundary"),
        P(f"{BASE_MYSQL} MySQLdb\n", COMPILE_MYSQL, id="mysqldb-name-in-compile-mysql"),
        P(f"{BASE_MYSQL} CLIENT\n", DJANGO_MYSQL, id="client-in-the-boundary"),
        P(f"{BASE_MYSQL} FIELD_TYPE\n", COMPILE_MYSQL, id="field-type-in-compile-mysql"),
        P(f"{BASE_MYSQL} conversions\n", DJANGO_MYSQL, id="conversions-in-the-boundary"),
        P("from django.db.backends.mysql.introspection import FIELD_TYPE\n", DJANGO_MYSQL,
          id="introspection-field-type-in-the-boundary"),
        P("import django.db.backends.mysql.base as b\n\ndef f():\n    return b.MySQLdb\n",
          COMPILE_MYSQL, id="mysqldb-attribute-in-compile-mysql"),
        P("import django.db.backends.mysql.base as b\n\ndef f():\n    return getattr(b, 'Database')\n",
          DJANGO_MYSQL, id="database-getattr-in-the-boundary"),
    ],
)  # fmt: skip
def test_the_driver_names_django_mysql_base_re_exports_stay_rejected_in_the_mysql_modules(
    source, path
):
    # when
    codes = distinct_codes(source, path)
    # then
    assert "A104" in codes


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P("import MySQLdb\n", COMPILE_MYSQL, id="compile-mysql"),
        P("import MySQLdb\n", DJANGO_MYSQL, id="boundary"),
        P("import MySQLdb.cursors\n", COMPILE_MYSQL, id="submodule"),
        P("from MySQLdb import connect\n", COMPILE_MYSQL, id="from-import"),
        P("from MySQLdb.constants import CLIENT\n", DJANGO_MYSQL, id="from-submodule"),
        P("import MySQLdb as db\n", ENGINE, id="engine"),
        P("import pymysql\n", COMPILE_MYSQL, id="pymysql"),
    ],
)  # fmt: skip
def test_importing_mysqlclient_is_rejected_anywhere_in_src(source, path):
    # when
    outcome = rule_outcome(source, path, "A101", "is banned in src")
    # then
    assert outcome == (["A101"], True)


@pytest.mark.parametrize(
    "module",
    [
        "django.db.backends.mysql.features",
        "django.db.backends.mysql.operations",
        "django.db.backends.mysql.client",
        "django.db.backends.mysql.creation",
    ],
)
def test_the_reused_django_mysql_modules_are_not_banned_in_the_boundary(module):
    # given
    source = f"import {module}\n"
    # when
    outcome = rule_outcome(source, DJANGO_MYSQL, "A101", "is banned")
    # then
    assert outcome[1] is False


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{BASE_MYSQL} DatabaseWrapper as BaseWrapper\n", DJANGO_MYSQL, id="wrapper-in-the-boundary"),
        P(f"{BASE_MYSQL} DatabaseWrapper as BaseWrapper\n", COMPILE_MYSQL, id="wrapper-in-compile"),
        P("import django.db.backends.mysql.base\n", DJANGO_MYSQL, id="plain-import"),
        P("import django.db.backends.mysql.base as b\n", ENGINE, id="aliased-import"),
        P("from django.db.backends.mysql import base\n", DJANGO_MYSQL, id="from-the-package"),
        P("from django.db.backends.mysql import base as b\n", QUERY, id="from-the-package-aliased"),
        P("from django.db.backends.mysql.introspection import DatabaseIntrospection\n",
          DJANGO_MYSQL, id="introspection-class"),
        P("import django.db.backends.mysql.introspection\n", COMPILE_MYSQL, id="introspection-module"),
        P("from django.db.backends.mysql import introspection\n", DJANGO_MYSQL,
          id="introspection-from-the-package"),
        P("from django.contrib.gis.db.backends.mysql.base import DatabaseWrapper\n", DJANGO_MYSQL,
          id="gis-base"),
        P("from django.contrib.gis.db.backends.mysql import introspection\n", DJANGO_MYSQL,
          id="gis-introspection"),
    ],
)  # fmt: skip
def test_django_modules_that_import_mysqlclient_are_banned_in_src(source, path):
    # when
    outcome = rule_outcome(source, path, "A101", "imports MySQLdb")
    # then
    assert outcome[1] is True


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P("from django.db.backends.mysql import features\n", DJANGO_MYSQL, id="package-features"),
        P("from django.db.backends.mysql import client, creation\n", DJANGO_MYSQL,
          id="package-client-creation"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/postgresql.py", id="postgresql-base-stays-allowed"),
        P("from django.db.backends.base.introspection import BaseDatabaseIntrospection\n",
          DJANGO_MYSQL, id="base-introspection"),
        P("from django.db.backends.mysql.features import DatabaseFeatures\n", DJANGO_MYSQL,
          id="features-module"),
    ],
)  # fmt: skip
def test_the_mysql_modules_that_do_not_import_mysqlclient_and_postgresql_base_are_not_banned(
    source, path
):
    # when
    outcome = rule_outcome(source, path, "A101", "imports MySQLdb")
    # then
    assert outcome[1] is False
