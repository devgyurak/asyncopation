"""`aq(related_manager)` after `prefetch_related` reads the prefetched rows (ADR-0006 Validation).

Kept apart from the differential module: no statement runs, so these tests need no async driver
and are not expected to fail on MySQL/MariaDB.
"""

from tests.support.alist_cases import (
    alist_of_a_prefetched_manager,
    alist_of_all_of_a_prefetched_manager,
)


async def test_alist_of_a_prefetched_related_manager_issues_no_statement(async_env):
    # when
    outcome = await alist_of_a_prefetched_manager()
    # then
    assert outcome.statements == []
    assert len(outcome.async_pks) > 0
    assert outcome.async_pks == outcome.sync_pks


async def test_alist_of_all_of_a_prefetched_related_manager_issues_no_statement(async_env):
    # when
    outcome = await alist_of_all_of_a_prefetched_manager()
    # then
    assert outcome.statements == []


async def test_alist_of_all_of_a_prefetched_related_manager_returns_the_prefetched_instances(
    async_env,
):
    # when
    outcome = await alist_of_all_of_a_prefetched_manager()
    # then
    assert outcome.expected_count > 0
    assert outcome.same_objects == [True] * outcome.expected_count
