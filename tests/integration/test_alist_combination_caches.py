"""`alist()` of a combination (`|`, `&`, `^`, union ...) never fills a QuerySet the caller holds.

Django returns an operand itself when one side is empty (`none() | qs`, `none().union(qs)`).

On MySQL and MariaDB this test xfails before its assertion (no async driver until #7), so the unit
tests in `tests/unit/query/test_chaining.py` carry the behaviour there.
"""

import pytest

from tests.support.alist_cases import (
    FILLED_OPERAND_SHAPES,
    alist_of_a_combination_with_a_filled_operand,
    alist_of_all_of_a_user_method_result_over_a_prefetched_manager,
)
from tests.support.aq_wrapper import COMBINATIONS, user_caches_after_alist_of
from tests.support.backends import XFAIL_UNTIL_ASYNCMY


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("build", COMBINATIONS)
async def test_alist_of_a_combination_leaves_the_callers_querysets_unevaluated(async_env, build):
    # when
    caches = await user_caches_after_alist_of(build)
    # then
    assert caches == [None] * 4


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize(("shape", "expected"), FILLED_OPERAND_SHAPES)
async def test_alist_of_a_combination_with_a_filled_operand_makes_the_statements_django_does(
    async_env, shape, expected
):
    # when
    outcome = await alist_of_a_combination_with_a_filled_operand(shape)
    # then
    assert outcome.sync_statements == expected
    assert outcome.statements == expected
    assert outcome.row_count > 0
    assert outcome.same_pks


@XFAIL_UNTIL_ASYNCMY
async def test_all_of_a_user_method_result_over_a_prefetched_manager_queries_as_django_does(
    async_env,
):
    # when
    outcome = await alist_of_all_of_a_user_method_result_over_a_prefetched_manager()
    # then
    assert outcome.sync_statements == 1
    assert outcome.statements == 1
    assert outcome.row_count > 0
    assert outcome.same_pks
