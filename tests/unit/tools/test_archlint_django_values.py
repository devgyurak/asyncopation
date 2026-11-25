"""Django modules, classes and aliases that could smuggle out the connection handler (A102)."""

import pytest

from tests.support.guards import MODEL_CLASS, MODELS, distinct_codes, rule_outcome

P = pytest.param
QUERY = "src/asyncopation/query/x.py"
AS_VALUE = "used as a value"
REACHED = "reached through a Django import"
COMPUTED = "`getattr` with a computed name on a Django object"


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(MODELS + "def f():\n    a, b = models, 1\n    return a, b\n", AS_VALUE, id="unpacking"),
        P(MODELS + "def f():\n    for m in (models,):\n        return m\n", AS_VALUE, id="for-loop"),
        P(MODELS + "X = {'m': models}\n", AS_VALUE, id="dict-value"),
        P(MODELS + "X = [models]\n", AS_VALUE, id="list-element"),
        P(MODELS + "def f():\n    return models\n", AS_VALUE, id="returned"),
        P(MODELS + "def f(g):\n    g(models)\n", AS_VALUE, id="argument"),
        P(MODELS + "class C:\n    m = models\n", AS_VALUE, id="class-attribute"),
        P(MODELS + "X = None or models\n", AS_VALUE, id="or-expression"),
        P(MODELS + "def f():\n    return (m := models)\n", AS_VALUE, id="walrus"),
        P("import django\n\nX = [django]\n", AS_VALUE, id="bare-django"),
        P(MODELS + "def g():\n    return models.base\n", AS_VALUE, id="returned-submodule"),
        P("from django.db import models as m\n\ndef f():\n    return m.base\n", AS_VALUE,
          id="returned-submodule-through-alias"),
        P(MODELS + "def f():\n    return models.sql\n", AS_VALUE, id="returned-sql-submodule"),
        P(MODELS + "def f():\n    return getattr(models.sql, 'query')\n", AS_VALUE,
          id="constant-getattr-of-a-submodule-chain"),
        P(MODELS + "def f():\n    return (lambda x: x)(models.base).connections\n", AS_VALUE,
          id="lambda"),
        P(MODELS + "def f():\n    return [models.base][0].connections\n", AS_VALUE,
          id="list-subscript"),
        P(MODELS + "def f():\n    return vars(models.base)['connections']\n", AS_VALUE,
          id="vars-argument"),
        P("import django.db.models\n\nx = django.db.models\n", AS_VALUE, id="dotted-assignment"),
        P("import django.db.models as m\n\nx = m.query\n", AS_VALUE, id="aliased-submodule"),
        P(MODELS + "def f():\n    return getattr(models, 'base')\n", AS_VALUE,
          id="constant-getattr-submodule"),
        P(MODELS + "def f():\n    b = models.base\n    return b.connections['d']\n", AS_VALUE,
          id="module-alias"),
        P(MODELS + "def f():\n    b: object = models.base\n    return b.connections\n", AS_VALUE,
          id="annassign-module-alias"),
        P(MODELS + "def f():\n    return (b := models.base).connections\n", AS_VALUE,
          id="walrus-module-alias"),
        P(MODELS + "def f(m=models):\n    return m.base.connections\n", AS_VALUE,
          id="default-argument"),
        P(MODELS + "def f(*, m=models):\n    return m.base.connections\n", AS_VALUE,
          id="keyword-only-default"),
    ],
)  # fmt: skip
def test_a_django_module_used_as_a_value_is_reported(source, rule):
    # when
    outcome = rule_outcome(source, QUERY, "A102", rule)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    "source",
    [
        P(MODELS + "def f():\n    return models.CASCADE\n", id="constant-value"),
        P(MODELS + "class A(models.Model):\n    f = models.ForeignKey('x', on_delete=models.CASCADE)\n",
          id="model-definition"),
        P(MODELS + "def f():\n    return models.Model\n", id="chain"),
        P(MODELS + "def f(a):\n    return isinstance(a, models.Model)\n", id="isinstance"),
        P(MODELS + "def f():\n    return getattr(models, 'Model')\n", id="constant-getattr"),
        P(MODELS + "def f():\n    return getattr(models.base, 'Model')\n",
          id="constant-getattr-submodule"),
        P(MODELS + "def f():\n    return getattr(models, 'base').x\n",
          id="constant-getattr-submodule-chain"),
        P("import django\n\ndjango.setup()\n", id="setup-call"),
        P("from django.conf import settings\n\ndef f(g):\n    g(settings)\n", id="settings-argument"),
        P("from django.conf import settings\n\ndef f(g):\n    g(settings.DEBUG)\n",
          id="settings-attribute"),
        P("from django.db.models import Q\n\ndef f(g):\n    g(Q)\n", id="class-argument"),
        P("import django.db.models\n\nclass A(django.db.models.Model):\n    pass\n",
          id="dotted-base-class"),
        P("def f(self):\n    return self._django.connections\n", id="unrelated-attribute-chain"),
    ],
)  # fmt: skip
def test_django_values_that_are_not_modules_or_chains_stay_clean(source):
    # when
    codes = distinct_codes(source, QUERY)
    # then
    assert codes == []


@pytest.mark.parametrize(
    "source",
    [
        P(MODEL_CLASS + "def f():\n    return Model.connections\n", id="direct"),
        P(MODEL_CLASS + "def f():\n    a = Model\n    b = a\n    return b.connections\n",
          id="two-step"),
        P(MODEL_CLASS + "def f(m=Model):\n    return m.connections\n", id="default-argument"),
        P(MODEL_CLASS + "def f(*, m=Model):\n    return m.connections\n", id="keyword-only-default"),
        P(MODEL_CLASS + "def f():\n    b: object = Model\n    return b.connections\n",
          id="annassign"),
        P(MODEL_CLASS + "def f():\n    return (b := Model).connections\n", id="walrus"),
        P("from django.db.models import Model as M\n\ndef f():\n    return M.connections\n",
          id="asname"),
        P(MODEL_CLASS + "def f():\n    return Model.transaction\n", id="transaction"),
    ],
)  # fmt: skip
def test_a_banned_attribute_on_a_django_class_is_caught_through_every_alias_form(source):
    # when
    outcome = rule_outcome(source, QUERY, "A102", REACHED)
    # then
    assert outcome == (["A102"], True)


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        P(MODEL_CLASS + "def f(n):\n    return getattr(Model, n)\n", COMPUTED, id="variable-name"),
        P(MODEL_CLASS + "def f():\n    return getattr(Model, 'conn' + 'ections')\n", COMPUTED,
          id="concatenated-name"),
        P(MODEL_CLASS + "def f():\n    return getattr(getattr(Model, 'x'), 'connections')\n",
          "'connections'", id="nested-constant"),
        P(MODELS + "def f():\n    return getattr(models.base, 'conn' + 'ections')\n", COMPUTED,
          id="submodule-concatenated"),
        P(MODELS + "def f(n):\n    return getattr(models.base, n)\n", COMPUTED,
          id="submodule-variable"),
        P(MODELS + "def f():\n    return models.base.__dict__\n", "__dict__", id="dunder-dict"),
        P(MODELS + "def f():\n    return models.base.__getattribute__('connections')\n",
          "__getattribute__", id="dunder-getattribute"),
    ],
)  # fmt: skip
def test_dynamic_attribute_access_on_a_django_root_is_reported(source, rule):
    # when
    outcome = rule_outcome(source, QUERY, "A102", rule)
    # then
    assert outcome == (["A102"], True)
