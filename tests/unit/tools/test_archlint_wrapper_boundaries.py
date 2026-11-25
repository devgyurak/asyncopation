"""The real wrapper classes and the adapter boundary module (A102, A104; ADR-0008, ADR-0003)."""

import pytest

from tests.support.guards import distinct_codes, has_violation, rule_outcome

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
ENGINE = "src/asyncopation/engine/x.py"
COMPILE = "src/asyncopation/compile/x.py"
DJANGO = "src/asyncopation/_django/other.py"
PG_WRAPPER = "from asyncopation._django.postgresql import BaseWrapper"
RENAME = "do not rename `BaseWrapper`"
REBIND = "do not rebind `BaseWrapper`"
WRAPPER_ALIAS = "may only be imported as `BaseWrapper` by the `_django` backend modules"
ADAPTERS = "may only be imported by asyncopation/drivers/postgresql"
HELPER = "from asyncopation._django.postgresql_adapters import get_adapters_template\n"


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{PG_WRAPPER} as RealPG\n", COMPILE, id="renamed-in-compile"),
        P(f"{PG_WRAPPER} as RealPG\n", DJANGO, id="renamed-in-django-package"),
        P(f"{PG_WRAPPER} as RealPG\n", ENGINE, id="renamed-in-engine"),
        P("from asyncopation.compile.postgresql import BaseWrapper as W\n", ENGINE,
          id="renamed-re-export"),
        P("from asyncopation._django.postgresql import CompileOperations, BaseWrapper as W\n",
          COMPILE, id="renamed-among-several-names"),
    ],
)  # fmt: skip
def test_renaming_the_real_wrapper_class_on_import_is_reported(source, path):
    # when
    outcome = rule_outcome(source, path, "A102", RENAME)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{PG_WRAPPER}\nW = BaseWrapper\n", COMPILE, id="module-assignment"),
        P(f"{PG_WRAPPER}\nW: type = BaseWrapper\n", COMPILE, id="annotated-assignment"),
        P(f"{PG_WRAPPER}\n\nclass X:\n    W = BaseWrapper\n", COMPILE, id="class-attribute"),
        P("from asyncopation._django import postgresql\n\nW = postgresql.BaseWrapper\n", DJANGO,
          id="attribute-of-the-module"),
        P(f"{PG_WRAPPER}\nW, X = BaseWrapper, 1\n", COMPILE, id="tuple-assignment"),
        P(f"{PG_WRAPPER}\nW = BaseWrapper if True else None\n", COMPILE, id="conditional"),
        P(f"{PG_WRAPPER}\nW = [BaseWrapper]\n", COMPILE, id="container"),
        P(f"{PG_WRAPPER}\nW = {{BaseWrapper}}\n", COMPILE, id="set"),
        P(f"{PG_WRAPPER}\nW = 1, BaseWrapper\n", COMPILE, id="tuple-second-element"),
        P(f"{PG_WRAPPER}\nW = {{'a': BaseWrapper}}\n", COMPILE, id="dict-value"),
        P(f"{PG_WRAPPER}\nW = {{'a': 1, 'b': BaseWrapper}}\n", COMPILE, id="dict-mixed-values"),
        P(f"{PG_WRAPPER}\nW = {{**BaseWrapper}}\n", COMPILE, id="dict-unpacking"),
        P(f"{PG_WRAPPER}\nW = x or BaseWrapper\n", COMPILE, id="boolean-or"),
        P(f"{PG_WRAPPER}\nW = None if x else BaseWrapper\n", COMPILE, id="conditional-else"),
        P(f"{PG_WRAPPER}\n\ndef f(a=1, w=BaseWrapper):\n    return w\n", COMPILE,
          id="second-default"),
        P(f"{PG_WRAPPER}\n\ndef f(w=BaseWrapper, a=1):\n    return w\n", COMPILE,
          id="first-default"),
        P(f"{PG_WRAPPER}\n\nasync def f(w=BaseWrapper):\n    return w\n", DJANGO,
          id="async-default"),
        P(f"{PG_WRAPPER}\nf = lambda w=BaseWrapper: w\n", COMPILE, id="lambda-default"),
        P(f"{PG_WRAPPER}\nif (W := BaseWrapper):\n    pass\n", COMPILE, id="walrus"),
        P(f"{PG_WRAPPER}\n\ndef f(w=BaseWrapper):\n    return w\n", COMPILE, id="parameter-default"),
        P(f"{PG_WRAPPER}\n\ndef f(*, w=BaseWrapper):\n    return w\n", COMPILE,
          id="keyword-default"),
    ],
)  # fmt: skip
def test_rebinding_the_real_wrapper_class_is_reported(source, path):
    # when
    outcome = rule_outcome(source, path, "A102", REBIND)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{PG_WRAPPER}\nfrom asyncopation.compile.wrapper import CompileOnlyMixin\n\n"
          "class X(CompileOnlyMixin, BaseWrapper):\n    pass\n", COMPILE, id="mixin-first"),
        P(f"{PG_WRAPPER}\nfrom asyncopation.compile.wrapper import CompileOnlyMixin\n\n"
          "class X(CompileOnlyMixin, object, BaseWrapper):\n    pass\n", COMPILE,
          id="mixin-before-with-others-between"),
        P(f"{PG_WRAPPER}\nfrom asyncopation.compile.wrapper import CompileOnlyMixin\nfrom other import Other\n\n"
          "class X(Other, CompileOnlyMixin, BaseWrapper):\n    pass\n", COMPILE,
          id="another-base-before-the-mixin"),
        P(f"{PG_WRAPPER}\n\ndef f(w):\n    return isinstance(w, BaseWrapper)\n", COMPILE,
          id="isinstance"),
        P(f"{PG_WRAPPER}\n\nok = isinstance(1, BaseWrapper)\n", COMPILE, id="isinstance-assigned"),
        P(f"{PG_WRAPPER}\n\nw = {{**other, 'a': 1}}\n", COMPILE, id="dict-unpacking-without-it"),
        P("import asyncopation._django.postgresql as m\n\ndef f(w):\n    return isinstance(w, m.BaseWrapper)\n",
          DJANGO, id="attribute-in-the-django-package"),
        P("from asyncopation._django import postgresql as m\n\ndef f(w):\n    return isinstance(w, m.BaseWrapper)\n",
          COMPILE, id="attribute-in-compile"),
        P("from asyncopation._django import postgresql as m\n\ndef f():\n    return getattr(m, 'BaseWrapper')\n",
          COMPILE, id="constant-getattr-in-compile"),
        P("from asyncopation._django import postgresql as m\n\ndef f():\n    return getattr(m, 'BaseWrapper')\n",
          DJANGO, id="constant-getattr-in-the-django-package"),
    ],
)  # fmt: skip
def test_using_the_real_wrapper_class_by_name_is_still_allowed_in_compile(source, path):
    # when
    codes = distinct_codes(source, path)
    # then
    assert codes == []


@pytest.mark.parametrize(
    ("source", "path", "codes"),
    [
        P("from django.contrib.gis.db.backends.postgis.base import DatabaseWrapper\n", QUERY, ["A102", "A201", "A203"],
          id="gis-postgis"),
        P("from django.contrib.gis.db.backends.mysql.base import DatabaseWrapper\n", QUERY, ["A102", "A201", "A203"],
          id="gis-mysql"),
        P("from django.contrib.gis.db.backends.postgis.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/foo.py", ["A102", "A201"], id="gis-right-alias-wrong-path"),
        P("from django.db.backends.mysql.base import DatabaseWrapper as BaseWrapper\n", QUERY, ["A102", "A201", "A203"],
          id="mysql-outside-the-boundary"),
        P("from django.db.backends.mysql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/_django/foo.py", ["A102", "A201"], id="mysql-wrong-django-module"),
        P("from django.db.backends.mysql.base import DatabaseWrapper\n", QUERY, ["A102", "A201", "A203"], id="mysql-plain"),
        P("from django.db.backends.postgresql.base import DatabaseWrapper as BaseWrapper\n",
          "src/asyncopation/compile/postgresql.py", ["A102", "A201", "A203"], id="postgresql-in-compile"),
    ],
)  # fmt: skip
def test_a_backend_wrapper_class_is_imported_only_by_the_backend_boundary_modules(
    source, path, codes
):
    # when
    outcome = rule_outcome(source, path, "A102", WRAPPER_ALIAS)
    # then
    assert outcome == (codes, True)


@pytest.mark.parametrize(
    ("source", "path", "expected"),
    [
        P("def f(x):\n    return getattr(x, 'DatabaseWrapper')\n", QUERY, [], id="not-a-django-name"),
        P("import os\n\ndef f():\n    return getattr(os, 'DatabaseWrapper')\n", QUERY, [],
          id="stdlib-name"),
        P("from django.db.backends.postgresql import base as b\n\nW = getattr(b, 'DatabaseWrapper')\n",
          "src/asyncopation/_django/postgresql.py", ["A102", "A201"], id="boundary-module-too"),
    ],
)  # fmt: skip
def test_a_constant_getattr_of_the_backend_wrapper_only_matters_on_django_names(
    source, path, expected
):
    # when
    codes = distinct_codes(source, path)
    # then
    assert codes == expected


def test_a_constant_getattr_of_the_backend_wrapper_is_reported_like_the_attribute():
    # given
    source = (
        "from django.db.backends.postgresql import base as b\n\nW = getattr(b, 'DatabaseWrapper')\n"
    )
    # when
    outcome = rule_outcome(source, DJANGO, "A102", WRAPPER_ALIAS)
    # then
    assert outcome == (["A102", "A201"], True)


@pytest.mark.parametrize(
    ("source", "path", "codes", "flagged"),
    [
        P(HELPER, "src/asyncopation/engine/postgresql/x.py", ["A104"], True, id="engine-lookalike"),
        P(HELPER, "src/asyncopation/compile/postgresql.py", ["A104"], True, id="compile-lookalike"),
        P(HELPER, "src/asyncopation/drivers/postgresql/x.py", [], False, id="driver-package"),
        P(HELPER, "src/asyncopation/drivers/postgresql.py", [], False, id="driver-module"),
        P(HELPER, ENGINE, ["A104"], True, id="engine"),
        P(HELPER, COMPILE, ["A104"], True, id="compile"),
        P(HELPER, DJANGO, ["A104"], True, id="django-package"),
        P(HELPER, "src/asyncopation/drivers/mysql/x.py", ["A104"], True, id="other-driver"),
        P(HELPER, "src/asyncopation/drivers/postgresql_extra/x.py", ["A104"], True, id="lookalike-driver"),
        P("from asyncopation._django import postgresql_adapters\n", ENGINE, ["A104"], True,
          id="module-name"),
        P("import asyncopation._django.postgresql_adapters\n", ENGINE, ["A104"], True, id="plain-import"),
        P("from .postgresql_adapters import get_adapters_template\n", DJANGO, ["A104"], True,
          id="relative"),
        P("from asyncopation._django import postgresql_adapters\n",
          "src/asyncopation/drivers/postgresql/x.py", [], False, id="module-name-in-driver"),
    ],
)  # fmt: skip
def test_the_adapter_boundary_module_is_importable_only_by_the_postgresql_driver(
    source, path, codes, flagged
):
    # when
    outcome = rule_outcome(source, path, "A104", ADAPTERS)
    # then
    assert outcome == (codes, flagged)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{PG_WRAPPER}\n\nclass RealPG(BaseWrapper):\n    pass\n", COMPILE, id="no-mixin"),
        P(f"{PG_WRAPPER}\nfrom other import OtherMixin\n\nclass X(OtherMixin, BaseWrapper):\n    pass\n",
          COMPILE, id="another-mixin-first"),
        P(f"{PG_WRAPPER}\nfrom asyncopation.compile.wrapper import CompileOnlyMixin\n\n"
          "class PG(BaseWrapper, CompileOnlyMixin):\n    pass\n", COMPILE, id="mixin-last"),
        P(f"{PG_WRAPPER}\n\nclass RealPG(BaseWrapper):\n    pass\n", DJANGO, id="no-mixin-in-django"),
        P("from asyncopation._django import postgresql as m\n\nclass RealPG(m.BaseWrapper):\n    pass\n",
          DJANGO, id="attribute-base"),
    ],
)  # fmt: skip
def test_a_class_based_on_the_real_wrapper_must_list_the_mixin_first(source, path):
    # when
    outcome = rule_outcome(source, path, "A102", "must list `CompileOnlyMixin`")
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path"),
    [
        P(f"{PG_WRAPPER}\n\nSHARED = BaseWrapper({{}}, 'default')\n", COMPILE, id="instantiation"),
        P("from asyncopation._django import postgresql as m\n\ndef f():\n    return m.BaseWrapper({})\n",
          DJANGO, id="attribute-instantiation"),
    ],
)  # fmt: skip
def test_the_real_wrapper_class_is_never_instantiated(source, path):
    # when
    outcome = rule_outcome(source, path, "A102", "do not instantiate")
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "path", "codes", "flagged"),
    [
        P("import asyncopation\n\ndef f():\n    return asyncopation._django.postgresql_adapters.helper\n",
          DJANGO, ["A104"], True, id="dotted-chain-in-django"),
        P("import asyncopation\n\ndef f():\n    return asyncopation._django.postgresql_adapters.x\n",
          "src/asyncopation/drivers/postgresql/x.py", ["A102"], False, id="dotted-chain-in-driver"),
    ],
)  # fmt: skip
def test_the_dotted_adapter_boundary_path_follows_the_importer_rule(source, path, codes, flagged):
    # when
    outcome = rule_outcome(source, path, "A104", "may only be used by asyncopation/drivers")
    # then
    assert outcome == (codes, flagged)


@pytest.mark.parametrize(
    ("source", "path", "flagged"),
    [
        P("import asyncopation\n\ndef f():\n    return getattr(asyncopation, '_django').postgresql_adapters\n",
          "src/asyncopation/engine/x.py", True, id="getattr-of-the-package-object"),
        P("import asyncopation\n\ndef f():\n    return getattr(asyncopation, '_django')\n",
          "src/asyncopation/_django/x.py", False, id="allowed-inside-the-django-package"),
        P("def f(thing):\n    return getattr(thing, '_django')\n",
          "src/asyncopation/engine/x.py", False, id="an-unrelated-object"),
        P("import asyncopation\n\ndef f():\n    return getattr(asyncopation, '_other')\n",
          "src/asyncopation/engine/x.py", False, id="another-private-name"),
    ],
)  # fmt: skip
def test_the_package_object_is_not_reachable_through_a_constant_getattr(source, path, flagged):
    # when
    found = has_violation(source, path, "A102", "reaching `asyncopation._django` through a bound")
    # then
    assert found is flagged


def test_the_bare_adapter_boundary_module_is_an_adapter_import_too():
    # given
    source = (
        "import asyncopation\n\ndef f():\n    return asyncopation._django.postgresql_adapters\n"
    )
    # when
    outcome = rule_outcome(source, DJANGO, "A104", "may only be used by asyncopation/drivers")
    # then
    assert outcome == (["A104"], True)


GETATTR_CONN = (
    'import asyncopation\n\ndef f():\n    return getattr(asyncopation, "_django").connections.x\n'
)
GETATTR_ADAPTER = (
    'import asyncopation\n\ndef f():\n    return getattr(asyncopation._django, "postgresql_adapters")'
    ".adapters_template\n"
)
GETATTR_ADAPTER_OUTSIDE = 'import asyncopation\n\ndef f():\n    return getattr(asyncopation, "_django").postgresql_adapters.x\n'


@pytest.mark.parametrize(
    ("source", "path", "code", "fragment", "codes", "flagged"),
    [
        P(GETATTR_CONN, DJANGO, "A102", "reachable only from compile/scope.py", ["A102"], True,
          id="connections-in-the-django-package"),
        P(GETATTR_ADAPTER, DJANGO, "A104", "may only be used by", ["A104"], True,
          id="adapters-in-the-django-package"),
        P(GETATTR_CONN, ENGINE, "A102", "reachable only from compile/scope.py", ["A102"], True,
          id="connections-outside"),
        P(GETATTR_ADAPTER_OUTSIDE, ENGINE, "A104", "may only be used by", ["A102", "A104"], True,
          id="adapters-outside"),
        P('import asyncopation as a\n\ndef f():\n    return getattr(a, "_django").connections.x\n',
          DJANGO, "A102", "reachable only from compile/scope.py", ["A102"], True,
          id="connections-through-an-alias"),
        P('import asyncopation\n\ndef f():\n    return getattr(getattr(asyncopation, "_django"), '
          '"postgresql_adapters").adapters_template\n',
          DJANGO, "A104", "may only be used by", ["A104"], True, id="adapters-nested-getattr"),
        P('import asyncopation\n\ndef f():\n    return getattr(asyncopation, "_django", None).connections.x\n',
          DJANGO, "A102", "reachable only from compile/scope.py", ["A102"], True,
          id="connections-with-a-default"),
        P(GETATTR_CONN, "src/asyncopation/compile/scope.py", "A102", "reachable only from compile/scope.py",
          ["A102"], False, id="connections-in-the-scope-module-is-no-boundary-hit"),
        P(GETATTR_ADAPTER, "src/asyncopation/drivers/postgresql/x.py", "A104", "may only be used by",
          ["A102"], False, id="adapters-in-the-driver-is-no-a104"),
    ],
)  # fmt: skip
def test_constant_getattr_steps_reach_the_boundary_modules_like_attributes(
    source, path, code, fragment, codes, flagged
):
    # when
    outcome = rule_outcome(source, path, code, fragment)
    # then
    assert outcome == (codes, flagged)


@pytest.mark.parametrize(
    "source",
    [
        P('import asyncopation\n\ndef f():\n    return hasattr(asyncopation, "_django")\n',
          id="hasattr"),
        P('import asyncopation\n\ndef f():\n    return getattr(asyncopation, "_django")\n',
          id="no-chain"),
        P('import asyncopation\n\ndef f():\n    return hasattr(asyncopation._django, "connections")\n',
          id="hasattr-on-the-boundary-module"),
        P('import asyncopation\n\ndef f():\n    return getattr(hasattr(asyncopation, "_django"), "connections")\n',
          id="getattr-on-a-hasattr-result"),
    ],
)  # fmt: skip
def test_the_package_object_alone_is_not_a_boundary_module_in_the_django_package(source):
    # when
    codes = distinct_codes(source, DJANGO)
    # then
    assert codes == []


def test_another_asyncopation_attribute_is_not_a_boundary_module_outside_the_django_package():
    # given
    source = (
        'import asyncopation\n\ndef f():\n    return getattr(asyncopation, "engine").connections\n'
    )
    # when
    codes = distinct_codes(source, ENGINE)
    # then
    assert codes == []
