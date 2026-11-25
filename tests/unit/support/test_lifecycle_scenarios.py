"""Scenario helpers clean up after themselves, also when a wait times out."""

import asyncio

import pytest

from tests.support.lifecycle.helpers import entry_aliases
from tests.support.lifecycle.scenarios import (
    ConcurrentOpenScenario,
    LockCounter,
    OpeningScenario,
    count_locks,
    stuck_opener_close,
)


async def test_a_concurrent_open_scenario_that_times_out_releases_its_gate(configured):
    # given
    scenario = ConcurrentOpenScenario(configured, LockCounter(), within=0.3)
    # when
    with pytest.raises(TimeoutError):
        await scenario.run()
    # then
    assert configured.script.gate is None


async def test_a_concurrent_open_scenario_that_times_out_stops_its_tasks(configured):
    # given
    scenario = ConcurrentOpenScenario(configured, LockCounter(), within=0.3)
    with pytest.raises(TimeoutError):
        await scenario.run()
    # when
    stopped = scenario.tasks_stopped()
    # then
    assert stopped is True


async def test_an_opening_scenario_cleanup_releases_the_held_open(configured):
    # given
    scenario = OpeningScenario(configured)
    await scenario.begin()
    # when
    await scenario.cleanup()
    # then
    assert entry_aliases() == ["default"]


async def test_the_lock_counter_counts_each_acquisition(configured, monkeypatch):
    # given
    counter = count_locks(monkeypatch)
    lock = asyncio.Lock()
    # when
    async with lock:
        pass
    # then
    assert (counter.created, counter.acquired) == (1, 1)


async def test_a_hung_close_scenario_whose_opener_never_ends_still_returns_within_a_bound(opening):
    # given
    await opening.begin()
    # when
    hung = await stuck_opener_close(opening, 0.1)
    # then
    assert hung.total < 8.0


async def test_a_hung_close_scenario_whose_opener_never_ends_reports_it_cancelled(opening):
    # given
    await opening.begin()
    # when
    hung = await stuck_opener_close(opening, 0.1)
    # then
    assert type(hung.opener_outcome).__name__ == "CancelledError"
