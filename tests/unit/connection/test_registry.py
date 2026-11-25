"""The process-global registry: entries, server info, pool stats, in-flight counters, reset."""

import asyncio

from asyncopation.connection import configure, registry
from asyncopation.connection.lifecycle import ensure_driver
from asyncopation.drivers.base import PoolStats
from tests.support.lifecycle import FakeInfo, fresh_loop, in_flight_in_other_loop


def test_registry_is_empty_before_anything_opens(fake):
    # when
    empty = registry.is_empty()
    # then
    assert (empty, registry.entries(), registry.server_info("default")) == (True, [], None)


async def test_pool_stats_without_an_entry_are_zero(fake):
    # when
    stats = registry.pool_stats("default")
    # then
    assert stats == PoolStats(0, 0, 0)


async def test_entries_list_alias_and_running_loop(opened):
    # when
    entries = registry.entries()
    # then
    assert entries == [("default", asyncio.get_running_loop())]


async def test_registry_is_not_empty_while_a_driver_is_open(opened):
    # when
    empty = registry.is_empty()
    # then
    assert empty is False


async def test_registry_is_empty_after_close_although_server_info_is_kept(opened):
    # given
    from asyncopation.connection import close

    await close()
    # when
    state = (registry.is_empty(), registry.server_info("default") is not None)
    # then
    assert state == (True, True)


async def test_pool_stats_default_to_the_running_loop(opened):
    # when
    stats = registry.pool_stats("default")
    # then
    assert stats == opened.script.stats


async def test_pool_stats_of_a_loop_without_an_entry_are_zero(opened):
    # when
    stats = registry.pool_stats("default", fresh_loop())
    # then
    assert stats == PoolStats(0, 0, 0)


async def test_server_info_is_the_probe_result(opened):
    # when
    info = registry.server_info("default")
    # then
    assert info == FakeInfo((17, 2))


async def test_server_info_of_an_alias_that_never_opened_is_none(opened):
    # when
    info = registry.server_info("other")
    # then
    assert info is None


async def test_reset_drops_entries_server_info_and_stats(opened):
    # when
    registry.reset()
    # then
    assert (
        registry.entries(),
        registry.server_info("default"),
        registry.pool_stats("default"),
    ) == (
        [],
        None,
        PoolStats(0, 0, 0),
    )


async def test_reset_does_not_close_drivers(opened):
    # when
    registry.reset()
    # then
    assert opened.recorder.closes == 0


async def test_reset_then_configure_allows_opening_again(opened):
    # given
    registry.reset()
    configure()
    # when
    await ensure_driver("default")
    # then
    assert opened.recorder.opens == 2


async def test_in_flight_starts_at_zero(fake):
    # when
    count = registry.in_flight("default")
    # then
    assert count == 0


async def test_statement_started_and_finished_count_in_flight(flights):
    # given
    registry.statement_started("default")
    registry.statement_started("default")
    registry.statement_finished("default")
    # when
    count = registry.in_flight("default")
    # then
    assert count == 1


async def test_in_flight_is_counted_per_alias(flights):
    # given
    registry.statement_started("default")
    # when
    count = registry.in_flight("other")
    # then
    assert count == 0


async def test_in_flight_is_counted_per_loop(flights):
    # given
    registry.statement_started("default")
    # when
    other = await in_flight_in_other_loop("default")
    # then
    assert other == 0
