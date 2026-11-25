"""The ``register_contrib_types`` flag: ``build_config``, ``from_settings`` and the engines wiring."""

import pytest

from tests.support.drivers.contrib import built_flag, engine_prepared_flag, from_settings_flag


def test_build_config_registers_no_contrib_types_by_default():
    # when
    flag = built_flag()
    # then
    assert flag is False


@pytest.mark.parametrize("value", [True, False])
def test_build_config_carries_the_flag_through(value):
    # when
    flag = built_flag(register_contrib_types=value)
    # then
    assert flag is value


def test_from_settings_registers_no_contrib_types_by_default():
    # when
    flag = from_settings_flag()
    # then
    assert flag is False


@pytest.mark.parametrize("value", [True, False])
def test_from_settings_carries_the_flag_through(value):
    # when
    flag = from_settings_flag(register_contrib_types=value)
    # then
    assert flag is value


@pytest.mark.parametrize("installed", [True, False])
def test_engine_prepare_passes_whether_contrib_postgres_is_installed(installed):
    # when
    flag = engine_prepared_flag(installed=installed)
    # then
    assert flag is installed
