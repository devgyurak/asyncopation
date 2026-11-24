"""`open()` failures surface immediately as Django's exception classes and leave nothing behind."""

import pytest
from django.db import OperationalError

from asyncopation.drivers.base import PoolStats
from tests.support.drivers import (
    POSTGRES_ONLY,
    compare_open_failure,
    first_borrow_after_login_revoked,
    is_pool_timeout,
    reopen_after_password_change,
)

pytestmark = POSTGRES_ONLY

BAD_SETTINGS = [
    pytest.param({}, {"PASSWORD": "definitely-wrong"}, id="bad-password"),
    pytest.param({}, {"NAME": "no_such_database_xyz"}, id="unknown-database"),
    pytest.param({"assume_role": "no_such_role_xyz"}, {}, id="bad-assume-role"),
]


@pytest.mark.parametrize(("options", "overrides"), BAD_SETTINGS)
async def test_open_failure_has_the_class_django_raises_for_the_same_settings(
    readonly_db, options, overrides
):
    # when
    result = await compare_open_failure(options=options, overrides=overrides)
    # then
    assert result.sync_error is not None
    assert type(result.async_failure.error) is type(result.sync_error)


@pytest.mark.parametrize(("options", "overrides"), BAD_SETTINGS)
async def test_open_failure_is_raised_immediately_not_after_the_pool_timeout(
    readonly_db, options, overrides
):
    # when
    result = await compare_open_failure(options=options, overrides=overrides)
    # then
    assert result.async_failure.error is not None
    assert result.async_failure.elapsed < 5


async def test_failed_open_leaves_no_pool_behind(readonly_db):
    # when
    result = await compare_open_failure(overrides={"PASSWORD": "definitely-wrong"})
    # then
    assert result.async_failure.stats == PoolStats(in_use=0, idle=0, waiting=0)


async def test_failed_open_leaves_capabilities_unavailable(readonly_db):
    # when
    result = await compare_open_failure(overrides={"PASSWORD": "definitely-wrong"})
    # then
    assert type(result.async_failure.capabilities_error) is RuntimeError


async def test_failed_open_leaves_connection_unavailable(readonly_db):
    # when
    result = await compare_open_failure(overrides={"PASSWORD": "definitely-wrong"})
    # then
    assert type(result.async_failure.connection_error) is RuntimeError


async def test_open_can_be_retried_once_the_credentials_work(django_db_setup):
    # when
    outcome = await reopen_after_password_change("asyncopation_login_role")
    # then
    assert outcome.first_error is not None
    assert outcome.retry_error is None
    assert outcome.stats_after_retry.idle >= 1


async def test_first_borrow_with_an_empty_pool_fails_fast_as_operational_error(django_db_setup):
    # when
    outcome = await first_borrow_after_login_revoked("asyncopation_login_role")
    # then
    assert type(outcome.error) is OperationalError
    assert is_pool_timeout(outcome.error)
    assert outcome.elapsed < 3


async def test_failed_first_borrow_leaves_nothing_behind_after_close(django_db_setup):
    # when
    outcome = await first_borrow_after_login_revoked("asyncopation_login_role")
    # then
    assert outcome.stats_after_close == PoolStats(in_use=0, idle=0, waiting=0)


async def test_pool_stats_count_a_connection_that_is_still_opening_as_in_use(django_db_setup):
    # when
    outcome = await first_borrow_after_login_revoked("asyncopation_login_role")
    # then
    # psycopg_pool keeps retrying the connection in the background after the borrower timed out:
    # that slot is "in use" in pool_size - pool_available terms (PoolStats documents this).
    assert (outcome.stats.in_use, outcome.stats.idle) == (1, 0)
