"""`setup()` and `close()` (ADR-0004 Decisions 3 and 5), against a fake driver."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.connection import close, registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from asyncopation.drivers.base import PoolStats
from tests.support.lifecycle import (
    FakeCloseError,
    FakeInfo,
    FakeOpenError,
    SetupScenario,
    capture_logs,
    ensure_is_refused,
    entry_aliases,
    error_names,
    errors_logged,
    failing,
    mention_count,
)


async def test_setup_opens_every_configured_alias(configured):
    # when
    await setup()
    # then
    assert entry_aliases() == ["default", "other"]


async def test_setup_with_aliases_opens_only_those(configured):
    # when
    await setup(["other"])
    # then
    assert entry_aliases() == ["other"]


async def test_setup_twice_builds_each_driver_once(configured):
    # given
    await setup()
    # when
    await setup()
    # then
    assert len(configured.recorder.built) == 2


async def test_setup_probes_once_per_alias(configured):
    # when
    await setup()
    # then
    assert configured.recorder.probes == 2


async def test_close_removes_this_loops_entries(opened):
    # when
    await close()
    # then
    assert registry.entries() == []


async def test_close_closes_each_driver_once(opened):
    # when
    await close()
    # then
    assert opened.recorder.closes == 1


async def test_close_resets_pool_stats_to_zero(opened):
    # given
    before = registry.pool_stats("default")
    # when
    await close()
    # then
    assert before == opened.script.stats
    assert registry.pool_stats("default") == PoolStats(0, 0, 0)


async def test_close_keeps_the_server_info(opened):
    # when
    await close()
    # then
    assert registry.server_info("default") == FakeInfo((17, 2))


async def test_close_without_entries_is_a_no_op(configured):
    # when
    await close()
    # then
    assert configured.recorder.closes == 0


async def test_close_clears_the_closing_flag_so_the_alias_can_open_again(opened):
    # given
    await close()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


async def test_ensure_driver_during_close_raises_runtime_error(closing):
    # given
    await closing.begin()
    # when / then
    with pytest.raises(RuntimeError):
        await ensure_driver("default")


async def test_close_waits_for_the_statement_in_flight(opened, closing):
    # when
    await closing.begin()
    # then
    assert opened.recorder.closes == 0
    assert len(registry.entries()) == 1


async def test_close_completes_when_the_last_statement_finishes(opened, closing):
    # given
    await closing.begin()
    # when
    await closing.finish()
    # then
    assert opened.recorder.closes == 1
    assert registry.entries() == []


async def test_close_raises_timeout_error_when_a_statement_never_finishes(short_closing):
    # given
    await short_closing.begin()
    # when / then
    with pytest.raises(TimeoutError):
        await short_closing.join()


async def test_close_force_closes_after_the_timeout(opened, short_closing):
    # given
    await short_closing.begin()
    await failing(short_closing.join(), TimeoutError)
    # when
    closes = opened.recorder.closes
    # then
    assert closes == 1
    assert registry.entries() == []


async def test_close_error_from_the_driver_propagates(opened):
    # given
    opened.script.fail_close = True
    # when / then
    with pytest.raises(FakeCloseError):
        await close()


async def test_close_twice_is_idempotent(opened):
    # given
    await close()
    # when
    await close()
    # then
    assert opened.recorder.closes == 1


async def test_close_with_two_aliases_closes_each_driver_once(configured):
    # given
    await setup()
    # when
    await close()
    # then
    assert configured.recorder.closes == 2
    assert registry.entries() == []


async def test_close_with_one_failing_driver_still_closes_the_others(configured):
    # given
    await setup()
    configured.script.fail_closes = 1
    # when
    await failing(close(), FakeCloseError)
    # then
    assert configured.recorder.closes == 2
    assert registry.entries() == []


async def test_setup_with_an_unknown_alias_raises_improperly_configured(configured):
    # when / then
    with pytest.raises(ImproperlyConfigured):
        await setup(["no_such_alias"])


async def test_setup_failing_on_the_second_alias_closes_the_first(configured):
    # given
    configured.script.fail_open_after = 1
    # when
    await failing(setup(["default", "other"]), FakeOpenError)
    # then
    assert configured.recorder.closes == 1
    assert registry.entries() == []


async def test_setup_failing_on_the_second_alias_raises_the_open_error(configured):
    # given
    configured.script.fail_open_after = 1
    # when / then
    with pytest.raises(FakeOpenError):
        await setup(["default", "other"])


async def test_failed_setup_leaves_drivers_that_were_already_open_alone(opened):
    # given
    opened.script.fail_open_after = 1
    # when
    await failing(setup(["default", "other"]), FakeOpenError)
    # then
    assert opened.recorder.closes == 0
    assert entry_aliases() == ["default"]


async def test_cancelled_setup_closes_the_alias_it_already_opened(configured):
    # given
    scenario = SetupScenario(configured, ["default", "other"])
    await scenario.begin()
    # when
    await scenario.cancel()
    # then
    assert configured.recorder.closes == 1
    assert entry_aliases() == []


async def test_close_cancelled_while_waiting_still_closes_the_drivers(opened, closing):
    # given
    await closing.begin()
    # when
    await closing.cancel_close()
    # then
    assert opened.recorder.closes == 1
    assert registry.entries() == []


async def test_a_close_that_finishes_first_keeps_the_other_closes_mark(opened, overlapping):
    # when
    outcome = await overlapping.begin()
    # then
    assert error_names(outcome) == ["TimeoutError"]
    assert await ensure_is_refused("default") is True


async def test_setup_rollback_leaves_a_driver_another_task_opened_concurrently(concurrent_open):
    # when
    results = await concurrent_open.run()
    # then
    assert error_names(results) == ["ok", "FakeOpenError"]
    assert entry_aliases() == ["other"]


async def test_setup_rollback_closes_only_the_drivers_it_opened_itself(concurrent_open):
    # when
    await concurrent_open.run()
    # then
    assert concurrent_open.env.recorder.closes == 1


async def test_a_concurrent_ensure_driver_on_an_alias_being_rolled_back_raises_runtime_error(
    rollback,
):
    # given
    await rollback.begin()
    # when
    results = await rollback.concurrent_ensure()
    # then
    assert error_names(results) == ["FakeOpenError", "RuntimeError"]


async def test_a_concurrent_ensure_driver_during_a_rollback_leaves_no_open_driver(rollback):
    # given
    await rollback.begin()
    # when
    await rollback.concurrent_ensure()
    # then
    assert entry_aliases() == []
    assert rollback.env.recorder.opens == 1
    assert rollback.env.recorder.closes == 1


async def test_setup_rollback_forgets_the_entry_even_when_its_close_is_cancelled(rollback):
    # given
    await rollback.begin()
    # when
    await rollback.cancel_setup()
    # then
    assert rollback.entry_forgotten("default") is True


async def test_a_cancelled_close_logs_the_driver_close_failures_at_error(opened, closing, caplog):
    # given
    capture_logs(caplog)
    opened.script.fail_close = True
    await closing.begin()
    # when
    await closing.cancel_close()
    # then
    assert mention_count(errors_logged(caplog), "failed to close") == 1


async def test_an_uninterrupted_close_does_not_log_the_failure_it_raises(opened, caplog):
    # given
    capture_logs(caplog)
    opened.script.fail_close = True
    await failing(close(), FakeCloseError)
    # when
    logged = errors_logged(caplog)
    # then
    assert logged == []
