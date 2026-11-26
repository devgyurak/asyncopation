"""`alist()` of a combination (`|`, `&`, `^`, union ...) never fills a QuerySet the caller holds.

Django returns an operand itself when one side is empty (`none() | qs`, `none().union(qs)`).

On MySQL and MariaDB this test xfails before its assertion (no async driver until #7), so the unit
tests in `tests/unit/query/test_chaining.py` carry the behaviour there.
"""

import pytest

from tests.support.aq_wrapper import COMBINATIONS, user_caches_after_alist_of
from tests.support.backends import XFAIL_UNTIL_ASYNCMY


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("build", COMBINATIONS)
async def test_alist_of_a_combination_leaves_the_callers_querysets_unevaluated(async_env, build):
    # when
    caches = await user_caches_after_alist_of(build)
    # then
    assert caches == [None, None]
