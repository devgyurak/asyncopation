"""`aq(qs).alist()` failure, concurrency and cancellation behaviour against the live database."""

import pytest

from tests.support.alist_cases import (
    alist_cache_placement,
    alist_cache_placement_of_fetch_raise,
    cancelled_alist_then_next,
    concurrent_alists_on_one_wrapper,
    failing_statement,
    gathered_alists,
    gathered_union_values_on_one_wrapper,
    unknown_alias,
)
from tests.support.backends import POSTGRESQL_ONLY, XFAIL_UNTIL_ASYNCMY


@XFAIL_UNTIL_ASYNCMY
async def test_alist_of_a_failing_statement_raises_the_sync_exception_type(differential):
    # when
    result = await differential(failing_statement)
    # then
    assert result.sync_outcome.error is not None
    assert result.async_outcome.error == result.sync_outcome.error


@POSTGRESQL_ONLY
async def test_alist_of_a_failing_statement_raises_the_django_programming_error(differential):
    # when
    result = await differential(failing_statement)
    # then
    assert result.async_outcome.error == "django.db.utils.ProgrammingError"


async def test_alist_on_an_unknown_alias_raises_connection_does_not_exist(differential):
    # when
    result = await differential(unknown_alias)
    # then
    assert result.sync_outcome.error == "django.utils.connection.ConnectionDoesNotExist"
    assert result.async_outcome.error == result.sync_outcome.error


@XFAIL_UNTIL_ASYNCMY
async def test_gathered_alists_on_one_alias_each_return_their_own_result(async_env):
    # when
    outcome = await gathered_alists()
    # then
    assert outcome.asynchronous == outcome.sync


@POSTGRESQL_ONLY  # nothing is opened before #7 on MySQL/MariaDB, so "no connection in use" would pass trivially
async def test_gathered_alists_leave_no_connection_in_use(async_env):
    # when
    outcome = await gathered_alists()
    # then
    assert outcome.in_use_after == 0


@POSTGRESQL_ONLY
async def test_a_cancelled_alist_is_cancelled(async_env):
    # when
    outcome = await cancelled_alist_then_next()
    # then
    assert outcome.cancelled is True


@POSTGRESQL_ONLY
async def test_a_cancelled_alist_leaves_the_connection_usable_for_the_next_call(async_env):
    # when
    outcome = await cancelled_alist_then_next()
    # then
    assert outcome.next_rows == 5


@POSTGRESQL_ONLY
async def test_a_cancelled_alist_releases_its_connection(async_env):
    # when
    outcome = await cancelled_alist_then_next()
    # then
    assert outcome.in_use_after == 0


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("caller", [0, 1])
async def test_concurrent_alists_on_one_wrapper_return_the_cached_instances(async_env, caller):
    # when
    outcome = await concurrent_alists_on_one_wrapper()
    # then
    assert outcome.results[caller][0] is outcome.cached[0]


@XFAIL_UNTIL_ASYNCMY
async def test_alist_fills_the_wrapper_clone_cache_and_not_the_original(async_env):
    # when
    outcome = await alist_cache_placement()
    # then
    assert outcome.original_cache is None
    assert outcome.wrapper_cache is not None


@XFAIL_UNTIL_ASYNCMY
async def test_alist_of_a_fetch_raise_queryset_fills_the_wrapper_clone_and_not_the_users(async_env):
    # when
    outcome = await alist_cache_placement_of_fetch_raise()
    # then
    assert outcome.original_cache is None
    assert outcome.wrapper_cache is not None


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("caller", [0, 1])
async def test_concurrent_alists_on_one_wrapper_return_a_copy_of_the_cache(async_env, caller):
    # when
    outcome = await concurrent_alists_on_one_wrapper()
    # then
    assert outcome.result_is_the_cache_list[caller] is False


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize("caller", [0, 1])
async def test_gathered_alists_of_a_union_values_wrapper_match_sync(async_env, caller):
    # when
    outcome = await gathered_union_values_on_one_wrapper()
    # then
    assert outcome.asynchronous[caller] == outcome.sync


@POSTGRESQL_ONLY  # before #7 nothing compiles on MySQL/MariaDB, so "untouched" would pass trivially
async def test_gathered_alists_of_a_union_values_wrapper_leave_its_query_untouched(async_env):
    # when
    outcome = await gathered_union_values_on_one_wrapper()
    # then
    assert outcome.annotations_after == outcome.annotations_before
