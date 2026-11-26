"""Drift: the Django MySQL modules asyncopation reuses still import without mysqlclient."""

import pytest

from tests.support.mysql_scripts import blocked

MODULES = [
    "django.db.backends.mysql.features",
    "django.db.backends.mysql.operations",
    "django.db.backends.mysql.client",
    "django.db.backends.mysql.creation",
    "django.db.backends.mysql.compiler",
    "django.db.backends.base.introspection",
]


@pytest.mark.parametrize("module", MODULES)
def test_the_reused_django_module_imports_without_mysqldb(module):
    # when
    result = blocked(f"import {module}")
    # then
    assert result == "0 False"
