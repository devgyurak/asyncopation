"""`aq(related_manager)` after `prefetch_related` reads the prefetched rows (ADR-0006 Validation).

Kept apart from the differential module: reads that use the prefetched cache run no statement, so
they need no async driver and are not expected to fail on MySQL/MariaDB. Every case that queries
(`all().all()`, `aget`, `aget` of a missing pk, an unordered or empty manager's `afirst`) needs the
driver and xfails there until the asyncmy driver (#7).
"""

import pytest

from tests.support.alist_cases import (
    alist_of_a_prefetched_manager,
    alist_of_all_of_a_prefetched_manager,
    alist_of_all_of_all_of_a_prefetched_manager,
    instance_mode_after_a_read_over_a_shared_cache,
    read_of_a_prefetched_manager,
    relation_access_after_a_read_over_a_shared_cache,
)
from tests.support.backends import XFAIL_UNTIL_ASYNCMY


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


@XFAIL_UNTIL_ASYNCMY
async def test_alist_of_all_of_all_of_a_prefetched_related_manager_queries_as_django_does(
    async_env,
):
    # when
    outcome = await alist_of_all_of_all_of_a_prefetched_manager()
    # then
    assert outcome.sync_statements == 1
    assert outcome.statements == outcome.sync_statements


# (subject, read, the statements Django runs): an ordered manager reads the cache; `get()` and an
# unordered `first()` query again (MySQL/MariaDB need the driver for those).
_CACHED = [
    ("books", "acount", 0),
    ("books", "aexists", 0),
    ("books", "afirst", 0),
    ("books", "async for", 0),
    ("orders", "acount", 0),
    ("orders", "aexists", 0),
    ("orders", "async for", 0),
    ("empty", "acount", 0),
    ("empty", "aexists", 0),
    ("empty", "async for", 0),
]
_QUERIED = [
    ("books", "aget", 1),
    ("books", "aget-missing", 1),
    ("orders", "aget", 1),
    ("empty", "aget-missing", 1),
    ("orders", "afirst", 1),
    ("empty", "afirst", 1),
]


@pytest.mark.parametrize(("subject", "name", "statements"), _CACHED)
async def test_read_of_a_prefetched_related_manager_matches_django_from_the_cache(
    async_env, writable_db, subject, name, statements
):
    # when
    outcome = await read_of_a_prefetched_manager(subject, name)
    # then
    assert (outcome.sync_statements, outcome.statements, outcome.async_result) == (
        statements,
        outcome.sync_statements,
        outcome.sync_result,
    )


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize(("subject", "name", "statements"), _QUERIED)
async def test_read_of_a_prefetched_related_manager_matches_django_when_it_queries(
    async_env, writable_db, subject, name, statements
):
    # when
    outcome = await read_of_a_prefetched_manager(subject, name)
    # then
    assert (outcome.sync_statements, outcome.statements, outcome.async_result) == (
        statements,
        outcome.sync_statements,
        outcome.sync_result,
    )


# (subject, read): the engine builds a clone and queries, so its instances are FETCH_RAISE
# although the wrapper shares a cache filled in a user fetch mode.
_ENGINE_CLONES = [
    ("prefetched-books", "aget"),
    ("evaluated-orders", "aget"),
    ("evaluated-orders", "afirst"),
    ("empty-or-evaluated-orders", "aget"),
    ("empty-or-evaluated-orders", "afirst"),
]
# (subject, read): the instances come out of the shared cache and keep the mode they were loaded in.
_CACHE_READS = [
    ("prefetched-books", "alist"),
    ("prefetched-books", "afirst"),
    ("prefetched-books", "async for"),
    ("evaluated-orders", "alist"),
    ("evaluated-orders", "async for"),
    ("empty-or-evaluated-orders", "alist"),
]


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize(("subject", "read"), _ENGINE_CLONES)
async def test_a_relation_of_an_instance_an_engine_built_clone_loads_is_blocked(
    async_env, writable_db, subject, read
):
    # when
    access = await relation_access_after_a_read_over_a_shared_cache(subject, read)
    # then
    assert access == "FieldFetchBlocked"


@XFAIL_UNTIL_ASYNCMY
@pytest.mark.parametrize(("subject", "read"), _ENGINE_CLONES)
async def test_an_instance_an_engine_built_clone_loads_is_fetch_raise(
    async_env, writable_db, subject, read
):
    # when
    mode = await instance_mode_after_a_read_over_a_shared_cache(subject, read)
    # then
    assert mode == "FetchRaise"


@pytest.mark.parametrize(("subject", "read"), _CACHE_READS)
async def test_an_instance_read_from_the_shared_cache_keeps_its_fetch_mode(
    async_env, writable_db, subject, read
):
    # when
    mode = await instance_mode_after_a_read_over_a_shared_cache(subject, read)
    # then
    assert mode == "FetchOne"
