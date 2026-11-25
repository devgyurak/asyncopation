"""`configure()`: per-alias config built from DATABASES, no I/O, re-readable (ADR-0004 Decision 1)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.connection import close, configure, registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle import (
    PG_ENGINE,
    POSTGIS_ENGINE,
    FakeInfo,
    aliases_of,
    apply_change,
    capture_logs,
    entry_aliases,
    errors_logged,
    failing,
    info_messages,
    lazy_warnings,
    leave_closing_and_flight_in_a_closed_loop,
    mention_count,
    opener_is_the_opening_task,
    pg_engine,
    purge_and_report_leaks,
    uncopiable_options,
)


def test_configure_returns_none_and_builds_no_driver(fake):
    # when
    result = configure()
    # then
    assert result is None
    assert fake.recorder.built == []
    assert registry.is_empty()


def test_configure_prepares_every_alias_with_the_django_time_settings(fake):
    # when
    configure()
    # then
    assert sorted(fake.recorder.prepares) == [
        ("default", "fake_db", True, "UTC"),
        ("other", "fake_other_db", True, "UTC"),
    ]


def test_configure_twice_is_idempotent(fake):
    # given
    configure()
    # when
    second = configure()
    # then
    assert second is None
    assert registry.is_empty()


def test_configure_again_picks_up_a_renamed_database(fake):
    # given
    configure()
    fake.rename("default", "renamed_db")
    # when
    configure()
    # then
    assert fake.recorder.last_prepare("default").name == "renamed_db"


async def test_configure_with_an_open_driver_raises_runtime_error(opened):
    # when / then
    with pytest.raises(RuntimeError):
        configure()


async def test_configure_with_an_open_driver_keeps_the_driver(opened):
    # given
    with pytest.raises(RuntimeError):
        configure()
    # when
    aliases = registry.entries()
    # then
    assert aliases_of(aliases) == ["default"]


async def test_configure_after_close_succeeds(opened):
    # given
    await close()
    # when
    result = configure()
    # then
    assert result is None


async def test_configure_after_close_keeps_server_info_when_the_config_is_unchanged(opened):
    # given
    await close()
    # when
    configure()
    # then
    assert registry.server_info("default") == FakeInfo((17, 2))


async def test_configure_after_close_drops_server_info_when_the_config_changed(opened):
    # given
    await close()
    opened.rename("default", "renamed_db")
    # when
    configure()
    # then
    assert registry.server_info("default") is None


async def test_configure_after_close_drops_only_the_changed_aliases_server_info(configured):
    # given
    await setup(["default", "other"])
    await close()
    configured.rename("other", "renamed_db")
    # when
    configure()
    # then
    assert registry.server_info("default") == FakeInfo((17, 2))
    assert registry.server_info("other") is None


def test_configure_with_an_unsupported_engine_does_not_raise(fake):
    # given
    fake.use_unsupported_engine("default")
    # when
    result = configure()
    # then
    assert result is None


async def test_first_open_with_an_unsupported_engine_raises_improperly_configured(fake):
    # given
    fake.use_unsupported_engine("default")
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured):
        await ensure_driver("default")


async def test_first_open_with_an_unsupported_engine_leaves_no_entry(fake):
    # given
    fake.use_unsupported_engine("default")
    configure()
    await failing(ensure_driver("default"), ImproperlyConfigured)
    # when
    empty = registry.is_empty()
    # then
    assert empty


async def test_setup_skips_an_unsupported_alias_and_opens_the_others(fake):
    # given
    fake.use_unsupported_engine("other")
    configure()
    # when
    await setup()
    # then
    assert entry_aliases() == ["default"]


async def test_setup_logs_each_skipped_alias_at_info(fake, caplog):
    # given
    capture_logs(caplog)
    fake.use_unsupported_engine("other")
    fake.add_broken_alias("broken", ImportError("No module named 'psycopg'"))
    configure()
    # when
    await setup()
    # then
    messages = info_messages(caplog)
    assert mention_count(messages, "'other'") == 1
    assert mention_count(messages, "'broken'") == 1


async def test_setup_with_an_explicit_unsupported_alias_raises_improperly_configured(fake):
    # given
    fake.use_unsupported_engine("other")
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured):
        await setup(["other"])


async def test_setup_with_an_explicit_misconfigured_alias_raises_improperly_configured(fake):
    # given
    fake.add_alias("bad", "", PG_ENGINE)
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="cannot be used"):
        await setup(["bad"])


async def test_setup_with_a_single_string_raises_type_error(configured):
    # when / then
    with pytest.raises(TypeError):
        await setup("default")


async def test_setup_with_a_single_string_opens_nothing(configured):
    # given
    await failing(setup("default"), TypeError)
    # when
    aliases = entry_aliases()
    # then
    assert aliases == []


async def test_ensure_driver_without_configure_raises_improperly_configured_naming_the_app(fake):
    # when / then
    with pytest.raises(ImproperlyConfigured, match="INSTALLED_APPS"):
        await ensure_driver("default")


async def test_registry_reset_drops_the_configuration(opened):
    # given
    registry.reset()
    # when / then
    with pytest.raises(ImproperlyConfigured):
        await ensure_driver("default")


async def test_configure_after_reset_then_open_works_again(opened):
    # given
    registry.reset()
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


async def test_configure_while_an_open_is_in_progress_raises_runtime_error(opening):
    # given
    await opening.begin()
    # when / then
    with pytest.raises(RuntimeError):
        configure()


async def test_configure_while_another_loop_holds_a_driver_raises_runtime_error(foreign_driver):
    # when / then
    with pytest.raises(RuntimeError):
        configure()


async def test_configure_is_not_blocked_by_a_driver_of_a_closed_loop(leaked_driver):
    # when
    result = configure()
    # then
    assert result is None


async def test_dropping_a_closed_loops_driver_logs_an_error(leaked_driver, caplog):
    # given
    capture_logs(caplog)
    # when
    configure()
    # then
    assert mention_count(errors_logged(caplog), "closed without") == 1


async def test_dropped_driver_of_a_closed_loop_is_reported_by_take_leaked(leaked_driver):
    # given
    configure()
    # when
    leaked = registry.take_leaked()
    # then
    assert aliases_of(leaked) == ["default"]


async def test_take_leaked_clears_the_list(leaked_driver):
    # given
    configure()
    registry.take_leaked()
    # when
    again = registry.take_leaked()
    # then
    assert again == []


async def test_ensure_driver_after_a_rename_without_configure_raises_improperly_configured(
    configured,
):
    # given
    configured.rename("default", "renamed_after_configure")
    # when / then
    with pytest.raises(ImproperlyConfigured, match="changed since configure"):
        await ensure_driver("default")


async def test_ensure_driver_after_a_rename_opens_no_driver_and_probes_nothing(configured):
    # given
    configured.rename("default", "renamed_after_configure")
    await failing(ensure_driver("default"), ImproperlyConfigured)
    # when
    built = configured.recorder.built
    # then
    assert built == []
    assert registry.entries() == []
    assert registry.server_info("default") is None


async def test_configure_after_a_rename_makes_the_alias_usable_again(configured):
    # given
    configured.rename("default", "renamed_after_configure")
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert configured.recorder.last_prepare("default").name == "renamed_after_configure"
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


def test_configure_survives_one_alias_with_an_invalid_postgresql_setting(fake):
    # given
    fake.add_alias("bad", "", PG_ENGINE)
    # when
    result = configure()
    # then
    assert result is None


async def test_a_misconfigured_alias_does_not_stop_the_others_from_opening(fake):
    # given
    fake.add_alias("bad", "", PG_ENGINE)
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


async def test_a_misconfigured_alias_raises_improperly_configured_at_its_first_open(fake):
    # given
    fake.add_alias("bad", "", PG_ENGINE)
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="cannot be used"):
        await ensure_driver("bad")


async def test_a_misconfigured_alias_logs_a_warning_when_configured(fake, caplog):
    # given
    capture_logs(caplog)
    fake.add_alias("bad", "", PG_ENGINE)
    # when
    configure()
    # then
    assert mention_count(lazy_warnings(caplog), "'bad'") == 1


def test_configure_survives_an_engine_whose_driver_library_is_missing(fake):
    # given
    fake.add_broken_alias("broken", ImportError("No module named 'psycopg'"))
    # when
    result = configure()
    # then
    assert result is None


async def test_an_alias_with_a_missing_driver_library_raises_improperly_configured_at_open(fake):
    # given
    fake.add_broken_alias("broken", ImportError("No module named 'psycopg'"))
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="psycopg"):
        await ensure_driver("broken")


async def test_a_postgis_alias_is_unsupported_until_it_has_a_compile_wrapper(fake):
    # given
    fake.use_engine("default", POSTGIS_ENGINE)
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="not supported"):
        await ensure_driver("default")


async def test_options_that_cannot_be_deep_copied_do_not_break_configure(fake):
    # given
    fake.set_options("default", uncopiable_options())
    # when
    result = configure()
    # then
    assert result is None


async def test_options_that_cannot_be_deep_copied_still_open_a_driver(fake):
    # given
    fake.set_options("default", uncopiable_options())
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


@pytest.mark.parametrize(
    ("change", "alias"),
    [
        pytest.param("host", "default", id="host"),
        pytest.param("options", "default", id="options"),
        pytest.param("engine", "default", id="engine"),
        pytest.param("use_tz", "default", id="use-tz"),
        pytest.param("time_zone", "default", id="time-zone"),
    ],
)
async def test_opening_fails_closed_when_the_settings_changed_after_configure(
    configured, settings, change, alias
):
    # given
    apply_change(configured, settings, change)
    # when / then
    with pytest.raises(ImproperlyConfigured, match="changed since configure"):
        await ensure_driver(alias)


async def test_an_alias_added_after_configure_is_unknown(configured, settings):
    # given
    apply_change(configured, settings, "alias_added")
    # when / then
    with pytest.raises(ImproperlyConfigured, match="unknown database alias"):
        await ensure_driver("late")


async def test_a_bad_time_zone_on_a_secondary_alias_does_not_break_configure(fake):
    # given
    fake.add_alias("tz", "tz_db", PG_ENGINE)
    fake.override("tz", TIME_ZONE="Nowhere/Land")
    # when
    result = configure()
    # then
    assert result is None


async def test_a_bad_time_zone_raises_improperly_configured_at_that_aliass_open(fake):
    # given
    fake.add_alias("tz", "tz_db", PG_ENGINE)
    fake.override("tz", TIME_ZONE="Nowhere/Land")
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="cannot be used"):
        await ensure_driver("tz")


async def test_a_bad_time_zone_leaves_the_other_aliases_usable(fake):
    # given
    fake.add_alias("tz", "tz_db", PG_ENGINE)
    fake.override("tz", TIME_ZONE="Nowhere/Land")
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


def test_the_real_postgresql_engine_prepares_options_that_cannot_be_deep_copied():
    # given
    from tests.support.compile_wrappers import PG_SETTINGS
    from tests.support.lifecycle import uncopiable_options

    settings_dict = {**PG_SETTINGS, "OPTIONS": uncopiable_options()}
    # when
    factory = pg_engine().prepare("default", settings_dict, use_tz=True, time_zone="UTC")
    # then
    assert callable(factory)


def test_a_real_postgresql_alias_with_uncopiable_options_configures_without_error(fake):
    # given
    fake.add_alias("pg", "pg_db", PG_ENGINE)
    fake.set_options("pg", uncopiable_options())
    # when
    configure()
    # then
    assert registry.configs()["pg"].error is None


async def test_purge_clears_the_closing_mark_of_a_closed_loop(configured):
    # given
    state = await leave_closing_and_flight_in_a_closed_loop("default")
    # when
    purge_and_report_leaks()
    # then
    assert state.closing_mark_remains() is False


async def test_purge_clears_the_in_flight_counters_of_a_closed_loop(configured):
    # given
    state = await leave_closing_and_flight_in_a_closed_loop("default")
    # when
    purge_and_report_leaks()
    # then
    assert state.flights_remain() is False


async def test_purge_reports_the_driver_of_a_closed_loop_as_leaked(configured):
    # given
    await leave_closing_and_flight_in_a_closed_loop("default")
    # when
    leaked = purge_and_report_leaks()
    # then
    assert leaked == ["default"]


async def test_an_unexpected_error_from_one_engine_does_not_break_configure(fake):
    # given
    fake.add_broken_alias("odd", RuntimeError("boom"))
    # when
    result = configure()
    # then
    assert result is None


async def test_an_unexpected_error_from_one_engine_leaves_the_other_aliases_usable(fake):
    # given
    fake.add_broken_alias("odd", RuntimeError("boom"))
    configure()
    # when
    driver = await ensure_driver("default")
    # then
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


async def test_an_unexpected_error_from_one_engine_raises_improperly_configured_at_its_open(fake):
    # given
    fake.add_broken_alias("odd", RuntimeError("boom"))
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured, match="boom"):
        await ensure_driver("odd")


async def test_ensure_driver_records_the_task_that_opened_the_driver(configured):
    # when
    recorded = await opener_is_the_opening_task("default")
    # then
    assert recorded is True
