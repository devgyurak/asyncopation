"""`ensure_driver`: the single open path (build -> open -> probe -> inject), locks, retry, warnings."""

import pytest

from asyncopation.connection import registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle.fakes import (
    FakeInfo,
    FakeInjectError,
    FakeOpenError,
    FakeProbeError,
    counts,
)
from tests.support.lifecycle.helpers import (
    aliases_of,
    distinct,
    drivers_in,
    entry_aliases,
    error_names,
    errors_in,
    failing,
)
from tests.support.lifecycle.logs import (
    all_mention,
    capture_logs,
    errors_logged,
    lazy_warnings,
    mention_count,
)
from tests.support.lifecycle.loops import open_in_second_loop
from tests.support.lifecycle.scenarios import (
    cancel_opener_while_the_discarded_driver_closes,
    close_while_opening,
    count_locks,
    ensure_many,
    ensure_many_settled,
    reopen_after_cancelled_first_opener,
    setup_and_ensure_concurrently,
    waiter_meets_closing,
)


async def test_ensure_driver_builds_opens_probes_and_injects_once(configured):
    # when
    driver = await ensure_driver("default")
    # then
    assert configured.recorder.built == [driver]
    assert counts(configured.recorder)["opens"] == 1
    assert counts(configured.recorder)["probes"] == 1
    assert counts(configured.recorder)["injects"] == 1


async def test_ensure_driver_injects_the_probed_info_for_the_alias(configured):
    # when
    await ensure_driver("default")
    # then
    assert configured.recorder.injects == [("default", FakeInfo((17, 2)))]


async def test_ensure_driver_returns_the_same_driver_on_every_call(configured):
    # given
    first = await ensure_driver("default")
    # when
    second = await ensure_driver("default")
    # then
    assert second is first


async def test_twenty_concurrent_first_calls_share_one_driver(configured):
    # when
    drivers = await ensure_many("default", 20)
    # then
    assert distinct(drivers) == 1


async def test_twenty_concurrent_first_calls_open_and_probe_once(configured):
    # when
    await ensure_many("default", 20)
    # then
    assert counts(configured.recorder) == {
        "built": 1,
        "opens": 1,
        "closes": 0,
        "probes": 1,
        "injects": 1,
    }


async def test_fast_path_creates_and_acquires_no_lock(opened, monkeypatch):
    # given
    counter = count_locks(monkeypatch)
    # when
    await ensure_many("default", 5)
    # then
    assert (counter.created, counter.acquired) == (0, 0)


async def test_first_call_takes_a_lock_created_in_the_running_loop(configured, monkeypatch):
    # given
    counter = count_locks(monkeypatch)
    # when
    await ensure_driver("default")
    # then
    assert (counter.created, counter.acquired) == (1, 1)


async def test_failed_open_leaves_no_entry_and_no_server_info(configured):
    # given
    configured.script.fail_opens = 1
    # when
    await failing(ensure_driver("default"), FakeOpenError)
    # then
    assert registry.entries() == []


async def test_failed_open_leaves_no_server_info(configured):
    # given
    configured.script.fail_opens = 1
    # when
    await failing(ensure_driver("default"), FakeOpenError)
    # then
    assert registry.server_info("default") is None


async def test_failed_open_is_retried_by_the_next_call(configured):
    # given
    configured.script.fail_opens = 1
    await failing(ensure_driver("default"), FakeOpenError)
    # when
    driver = await ensure_driver("default")
    # then
    opened = driver.is_open  # ty: ignore[missing-attribute] - the fake driver
    assert opened is True
    assert configured.recorder.open_attempts == 2


async def test_failed_probe_leaves_no_entry_and_no_server_info(configured):
    # given
    configured.script.fail_probes = 1
    # when
    await failing(ensure_driver("default"), FakeProbeError)
    # then
    assert registry.entries() == []


async def test_failed_probe_leaves_no_server_info(configured):
    # given
    configured.script.fail_probes = 1
    # when
    await failing(ensure_driver("default"), FakeProbeError)
    # then
    assert registry.server_info("default") is None


async def test_failed_probe_closes_the_driver_it_opened(configured):
    # given
    configured.script.fail_probes = 1
    # when
    await failing(ensure_driver("default"), FakeProbeError)
    # then
    assert configured.recorder.opens == 1
    assert configured.recorder.closes == 1


async def test_failed_probe_is_retried_by_the_next_call(configured):
    # given
    configured.script.fail_probes = 1
    await failing(ensure_driver("default"), FakeProbeError)
    # when
    await ensure_driver("default")
    # then
    assert registry.server_info("default") == FakeInfo((17, 2))


async def test_probe_runs_once_per_alias_across_event_loops(configured):
    # given
    await ensure_driver("default")
    # when
    await open_in_second_loop("default")
    # then
    assert configured.recorder.probes == 1


async def test_second_event_loop_gets_its_own_driver(configured):
    # given
    main = await ensure_driver("default")
    # when
    second = await open_in_second_loop("default")
    # then
    assert second.driver is not main


async def test_second_event_loop_adds_an_entry_next_to_the_first(configured):
    # given
    await ensure_driver("default")
    # when
    second = await open_in_second_loop("default")
    # then
    assert aliases_of(second.entries_inside) == ["default", "default"]


async def test_close_in_another_loop_keeps_this_loops_entry(configured):
    # given
    await ensure_driver("default")
    # when
    second = await open_in_second_loop("default")
    # then
    assert aliases_of(second.entries_after_close) == ["default"]


async def test_lazy_open_logs_one_warning_recommending_the_lifespan_app(configured, caplog):
    # given
    capture_logs(caplog)
    # when
    await ensure_many("default", 5)
    # then
    assert len(lazy_warnings(caplog)) == 1


async def test_lazy_warning_recommends_the_lifespan_app(configured, caplog):
    # given
    capture_logs(caplog)
    # when
    await ensure_driver("default")
    # then
    assert all_mention(lazy_warnings(caplog), "LifespanApp") is True


async def test_lazy_open_in_a_second_loop_warns_again(configured, caplog):
    # given
    capture_logs(caplog)
    await ensure_driver("default")
    # when
    await open_in_second_loop("default")
    # then
    assert len(lazy_warnings(caplog)) == 2


async def test_lazy_open_of_another_alias_warns_again(configured, caplog):
    # given
    capture_logs(caplog)
    await ensure_driver("default")
    # when
    await ensure_driver("other")
    # then
    assert len(lazy_warnings(caplog)) == 2


async def test_setup_does_not_log_the_lazy_warning(configured, caplog):
    # given
    capture_logs(caplog)
    # when
    await setup()
    # then
    assert lazy_warnings(caplog) == []


async def test_aliases_open_independently(configured):
    # when
    await ensure_driver("other")
    # then
    assert entry_aliases() == ["other"]


async def test_one_failing_open_among_twenty_concurrent_calls_raises_exactly_once(configured):
    # given
    configured.script.fail_opens = 1
    # when
    results = await ensure_many_settled("default", 20)
    # then
    assert len(errors_in(results)) == 1


async def test_one_failing_open_among_twenty_concurrent_calls_still_yields_one_driver(configured):
    # given
    configured.script.fail_opens = 1
    # when
    results = await ensure_many_settled("default", 20)
    # then
    assert distinct(drivers_in(results)) == 1
    assert entry_aliases() == ["default"]


async def test_cancelled_first_opener_lets_the_next_caller_open(opening):
    # given
    await opening.begin()
    # when
    driver = await reopen_after_cancelled_first_opener(opening)
    # then
    assert driver.is_open
    assert entry_aliases() == ["default"]


async def test_cancelled_first_opener_leaves_no_entry(opening):
    # given
    await opening.begin()
    # when
    await opening.cancel()
    # then
    assert entry_aliases() == []


async def test_failed_probe_with_a_failing_close_still_raises_the_probe_error(configured):
    # given
    configured.script.fail_probes = 1
    configured.script.fail_closes = 1
    # when / then
    with pytest.raises(FakeProbeError):
        await ensure_driver("default")


async def test_concurrent_setup_and_ensure_driver_build_one_driver(configured):
    # when
    await setup_and_ensure_concurrently("default")
    # then
    assert counts(configured.recorder)["built"] == 1


async def test_concurrent_setup_and_ensure_driver_open_once(configured):
    # when
    await setup_and_ensure_concurrently("default")
    # then
    assert counts(configured.recorder)["opens"] == 1


async def test_failed_injection_closes_the_driver(configured):
    # given
    configured.script.fail_injects = 1
    # when
    await failing(ensure_driver("default"), FakeInjectError)
    # then
    assert configured.recorder.opens == 1
    assert configured.recorder.closes == 1


async def test_failed_injection_does_not_publish_the_server_info(configured):
    # given
    configured.script.fail_injects = 1
    # when
    await failing(ensure_driver("default"), FakeInjectError)
    # then
    assert registry.server_info("default") is None
    assert registry.entries() == []


async def test_failed_injection_is_retried_by_the_next_call(configured):
    # given
    configured.script.fail_injects = 1
    await failing(ensure_driver("default"), FakeInjectError)
    # when
    await ensure_driver("default")
    # then
    assert registry.server_info("default") == FakeInfo((17, 2))


async def test_close_waits_for_an_open_in_progress_and_leaves_nothing_open(opening):
    # given
    await opening.begin()
    # when
    results = await close_while_opening(opening)
    # then
    assert error_names(results) == ["RuntimeError", "ok"]
    assert registry.entries() == []


async def test_close_closes_the_driver_the_interrupted_open_had_built(opening):
    # given
    await opening.begin()
    # when
    await close_while_opening(opening)
    # then
    assert opening.env.recorder.opens == 1
    assert opening.env.recorder.closes == 1


async def test_a_caller_waiting_on_the_open_lock_that_meets_closing_raises_runtime_error(
    parked_opening,
):
    # given
    await parked_opening.begin()
    # when
    results = await waiter_meets_closing(parked_opening)
    # then
    assert error_names(results) == ["RuntimeError", "RuntimeError", "ok"]


async def test_the_queued_caller_that_meets_closing_opens_no_second_driver(parked_opening):
    # given
    await parked_opening.begin()
    # when
    await waiter_meets_closing(parked_opening)
    # then
    assert len(parked_opening.env.recorder.built) == 1


async def test_a_failing_close_after_a_failed_probe_is_logged_at_error(configured, caplog):
    # given
    capture_logs(caplog)
    configured.script.fail_probes = 1
    configured.script.fail_closes = 1
    # when
    await failing(ensure_driver("default"), FakeProbeError)
    # then
    assert mention_count(errors_logged(caplog), "discarded driver") == 1


async def test_a_failing_close_after_a_failed_injection_is_logged_at_error(configured, caplog):
    # given
    capture_logs(caplog)
    configured.script.fail_injects = 1
    configured.script.fail_closes = 1
    # when
    await failing(ensure_driver("default"), FakeInjectError)
    # then
    assert mention_count(errors_logged(caplog), "discarded driver") == 1


async def test_the_discarded_driver_is_closed_while_the_entry_lock_is_held_even_if_cancelled(
    configured,
):
    # when
    seen = await cancel_opener_while_the_discarded_driver_closes(configured)
    # then
    assert seen.task_done_during_close is False
    assert seen.lock_held_during_close is True


async def test_the_cancelled_opener_ends_cancelled_after_the_discarded_driver_closed(configured):
    # when
    seen = await cancel_opener_while_the_discarded_driver_closes(configured)
    # then
    assert error_names([seen.error]) == ["CancelledError"]
    assert seen.driver_open_after is False


async def test_the_entry_lock_is_free_after_a_cancelled_discard(configured):
    # when
    seen = await cancel_opener_while_the_discarded_driver_closes(configured)
    # then
    assert seen.lock_held_after is False
