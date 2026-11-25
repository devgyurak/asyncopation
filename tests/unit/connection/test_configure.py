"""`configure()`: per-alias config built from DATABASES, no I/O, re-readable (ADR-0004 Decision 1)."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.connection import close, configure, registry, setup
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle import FakeInfo, aliases_of


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
    with pytest.raises(ImproperlyConfigured):
        await ensure_driver("default")
    # when
    empty = registry.is_empty()
    # then
    assert empty


async def test_setup_with_an_unsupported_engine_raises_improperly_configured(fake):
    # given
    fake.use_unsupported_engine("other")
    configure()
    # when / then
    with pytest.raises(ImproperlyConfigured):
        await setup()


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
