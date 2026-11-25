"""The hstore/citext OID lookup happens once per ``open()``, never per pooled connection (#43)."""

import pytest

from tests.support.drivers import POSTGRES_ONLY
from tests.support.drivers.contrib import (
    catalog_queries_across_close_and_open,
    catalog_queries_after_growing_the_pool,
    catalog_queries_of_one_open,
    reconnect_after_backend_termination,
)

pytestmark = POSTGRES_ONLY

TYPE_NAMES = ["citext", "hstore"]


@pytest.mark.parametrize("min_size", [1, 3], ids=["one-connection", "three-connections"])
async def test_open_looks_up_hstore_and_citext_once_each(django_db_setup, min_size):
    # given
    pool = {"min_size": min_size, "max_size": 4}
    # when
    names = await catalog_queries_of_one_open(pool=pool)
    # then
    assert sorted(names) == TYPE_NAMES


async def test_borrowing_more_connections_adds_no_catalog_query(django_db_setup):
    # when
    names = await catalog_queries_after_growing_the_pool(4)
    # then
    assert sorted(names) == TYPE_NAMES


async def test_close_then_open_looks_the_oids_up_again(django_db_setup):
    # when
    names = await catalog_queries_across_close_and_open()
    # then
    assert sorted(names) == sorted(TYPE_NAMES * 2)


async def test_a_driver_with_the_flag_off_runs_no_catalog_query(django_db_setup):
    # given
    pool = {"min_size": 2, "max_size": 2}
    # when
    names = await catalog_queries_of_one_open(pool=pool, register=False)
    # then
    assert names == []


async def test_a_replacement_connection_after_pg_terminate_backend_reads_hstore_as_dict(
    readonly_db,
):
    # when
    outcome = await reconnect_after_backend_termination()
    # then
    assert outcome.new_pid != outcome.old_pid
    assert outcome.value == {"a": "1"}


async def test_a_replacement_connection_runs_no_further_catalog_query(readonly_db):
    # when
    outcome = await reconnect_after_backend_termination()
    # then
    assert sorted(outcome.catalog_queries) == TYPE_NAMES
