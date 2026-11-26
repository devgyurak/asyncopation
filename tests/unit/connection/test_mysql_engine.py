"""`engines`: the MySQL/MariaDB engine (prepare, pure probe from the driver, inject)."""

import dataclasses

import pytest
from django.core.exceptions import ImproperlyConfigured

from asyncopation.compile.server_info import MySQLServerInfo, PostgreSQLServerInfo
from asyncopation.connection.engines import FACTORIES, Engine
from asyncopation.drivers.mysql import MySQLDriver
from tests.support.drivers import set_attribute
from tests.support.lifecycle.fakes import FakeInfo
from tests.support.mysql_driver_wrappers import MY_SETTINGS, registered_wrapper
from tests.support.mysql_engine import (
    MY_ENGINE,
    StrictDriver,
    my_engine,
    other_payload,
    prepared_factory,
    probe_values,
    probed,
    probed_fields,
    touched_by_probe,
)


def test_the_mysql_engine_string_is_registered():
    # when
    keys = set(FACTORIES)
    # then
    assert MY_ENGINE in keys


def test_the_mysql_factory_returns_an_engine():
    # when
    engine = my_engine()
    # then
    assert isinstance(engine, Engine)


def test_the_mysql_engine_is_a_frozen_dataclass():
    # given
    engine = my_engine()
    # when / then
    with pytest.raises(dataclasses.FrozenInstanceError):
        set_attribute(engine, "probe", None)


def test_probe_builds_server_info_from_the_driver_server_data():
    # when
    info = probed(StrictDriver())
    # then
    assert info == MySQLServerInfo(**probe_values())


def test_probe_carries_all_six_fields():
    # when
    fields = probed_fields(StrictDriver())
    # then
    assert fields == probe_values()


def test_probe_reads_only_capabilities_and_server_data():
    # when
    touched = touched_by_probe()
    # then
    assert set(touched) <= {"capabilities", "server_data"}
    assert "server_data" in touched


def test_probe_keeps_sql_mode_as_a_string():
    # when
    info = probed(StrictDriver())
    # then
    assert isinstance(info.sql_mode, str)


def test_probe_of_a_driver_without_server_data_raises_type_error():
    # given
    driver = StrictDriver(with_server_data=False)
    # when / then
    with pytest.raises(TypeError):
        probed(driver)


@pytest.mark.parametrize(
    "payload",
    [other_payload(), probe_values(), "8.4.11", 8],
    ids=["namespace", "plain-dict", "string", "int"],
)
def test_probe_of_a_driver_with_another_payload_type_raises_type_error(payload):
    # given
    driver = StrictDriver(payload=payload)
    # when / then
    with pytest.raises(TypeError):
        probed(driver)


def test_inject_installs_a_compile_wrapper_with_the_probed_server_data(my_alias):
    # given
    engine = my_engine()
    # when
    engine.inject(my_alias, dict(MY_SETTINGS), probed(StrictDriver()))
    # then
    assert registered_wrapper(my_alias).mysql_version == (8, 4, 11)


@pytest.mark.parametrize(
    "info",
    [FakeInfo((8, 4)), PostgreSQLServerInfo(pg_version=170000)],
    ids=["fake", "postgresql"],
)
def test_inject_rejects_server_info_of_another_engine(my_alias, info):
    # given
    engine = my_engine()
    # when / then
    with pytest.raises(TypeError):
        engine.inject(my_alias, dict(MY_SETTINGS), info)


def test_prepare_returns_a_zero_argument_driver_factory():
    # when
    factory = prepared_factory()
    # then
    assert callable(factory)


def test_the_driver_factory_builds_an_unopened_mysql_driver():
    # given
    factory = prepared_factory()
    # when
    driver = factory()
    # then
    assert isinstance(driver, MySQLDriver)
    with pytest.raises(RuntimeError):
        _ = driver.capabilities


def test_prepare_validates_the_settings_at_once():
    # when / then
    with pytest.raises(ImproperlyConfigured):
        prepared_factory(OPTIONS={"ssl_mode": "REQUIRED"})
