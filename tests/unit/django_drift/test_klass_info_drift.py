"""The `compiler.klass_info` keys the materializer reads still exist in Django (ledger, SQLCompiler row)."""

import pytest

from tests.support.django_drift import klass_info_key_problem


@pytest.mark.parametrize("key", ["model", "select_fields", "related_klass_infos"])
def test_klass_info_key_is_still_set_by_the_compiler_and_read_by_the_query_module(key):
    # when
    problem = klass_info_key_problem(key)
    # then
    assert problem is None
