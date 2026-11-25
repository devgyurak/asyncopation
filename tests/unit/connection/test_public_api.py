"""Public names (`asyncopation.setup`, `asyncopation.close`) and the Django app config."""

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

import asyncopation
import asyncopation.connection as connection
from asyncopation.apps import AsyncopationConfig
from asyncopation.connection import lifecycle, registry
from asyncopation.connection.lifecycle import ensure_driver
from tests.support.lifecycle.fakes import uncopiable_options
from tests.support.lifecycle.helpers import entry_aliases, prepared_aliases


async def test_top_level_setup_opens_the_configured_drivers(configured):
    # when
    await asyncopation.setup()
    # then
    assert entry_aliases() == ["default", "other"]


async def test_top_level_close_closes_the_drivers_of_the_running_loop(opened):
    # when
    await asyncopation.close()
    # then
    assert entry_aliases() == []


def test_top_level_configure_configures_the_connection_layer(fake):
    # when
    result = asyncopation.configure()
    # then
    assert (result, prepared_aliases(fake.recorder)) == (None, ["default", "other"])


@pytest.mark.parametrize("name", ["setup", "close", "configure"])
def test_connection_package_reexports_the_lifecycle_api(name):
    # when
    resolved = getattr(connection, name)
    # then
    assert resolved is getattr(lifecycle, name)


@pytest.mark.parametrize("name", ["does_not_exist"])
def test_unknown_top_level_name_raises_attribute_error(name):
    # when / then
    with pytest.raises(AttributeError):
        getattr(asyncopation, name)


def test_app_config_is_named_asyncopation():
    # when
    config = AsyncopationConfig("asyncopation", asyncopation)
    # then
    assert config.name == "asyncopation"


def test_harness_installs_the_app_config():
    # when
    config = apps.get_app_config("asyncopation")
    # then
    assert isinstance(config, AsyncopationConfig)


async def test_ready_configures_the_connection_layer(fake):
    # given
    config = AsyncopationConfig("asyncopation", asyncopation)
    # when
    config.ready()
    # then
    driver = await ensure_driver("default")
    assert driver.is_open  # ty: ignore[missing-attribute] - the fake driver


async def test_a_rename_after_ready_is_refused_until_configure_runs_again(fake):
    # given
    config = AsyncopationConfig("asyncopation", asyncopation)
    config.ready()
    fake.rename("default", "renamed_after_ready")
    # when / then
    with pytest.raises(ImproperlyConfigured, match="changed since configure"):
        await ensure_driver("default")


async def test_ready_survives_options_that_cannot_be_deep_copied(fake):
    # given
    fake.set_options("default", uncopiable_options())
    config = AsyncopationConfig("asyncopation", asyncopation)
    # when
    config.ready()
    # then
    assert registry.configs()["default"].error is None


async def test_ready_survives_an_alias_whose_driver_library_is_missing(fake):
    # given
    fake.add_broken_alias("broken", ImportError("No module named 'psycopg'"))
    config = AsyncopationConfig("asyncopation", asyncopation)
    # when
    config.ready()
    # then
    assert isinstance(registry.configs()["broken"].error, ImportError)


async def test_top_level_close_without_a_timeout_uses_the_default_close_timeout(
    flights, monkeypatch
):
    # given
    monkeypatch.setattr(lifecycle, "DEFAULT_CLOSE_TIMEOUT", 0.05)
    registry.statement_started("default")
    # when / then
    with pytest.raises(TimeoutError):
        await asyncopation.close()


async def test_top_level_close_with_an_explicit_timeout_uses_it(flights):
    # given
    registry.statement_started("default")
    # when / then
    with pytest.raises(TimeoutError):
        await asyncopation.close(timeout=0.05)
