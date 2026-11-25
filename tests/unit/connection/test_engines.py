"""`engines`: the Engine value and the lazy PostgreSQL factory (probe from capabilities, inject)."""

from types import SimpleNamespace

import pytest

from asyncopation.compile.server_info import PostgreSQLServerInfo
from asyncopation.connection.engines import FACTORIES, Engine
from tests.support.compile_wrappers import PG_SETTINGS
from tests.support.lifecycle import PG_ENGINE, forget_wrapper, injected_wrapper, pg_engine


def fake_driver(version: tuple[int, ...]) -> SimpleNamespace:
    return SimpleNamespace(capabilities=SimpleNamespace(server_version=version))


def test_factories_are_keyed_by_the_django_engine_string():
    # when
    keys = set(FACTORIES)
    # then
    assert PG_ENGINE in keys


def test_the_postgresql_factory_returns_an_engine():
    # when
    engine = pg_engine()
    # then
    assert isinstance(engine, Engine)


def test_engine_is_a_frozen_dataclass():
    # given
    engine = pg_engine()
    # when / then
    with pytest.raises(AttributeError):
        engine.probe = None


@pytest.mark.parametrize(
    ("server_version", "expected"),
    [
        pytest.param((17, 2), 170002, id="17.2"),
        pytest.param((16, 0), 160000, id="16.0"),
        pytest.param((9, 6), 90006, id="9.6"),
        pytest.param((14, 11), 140011, id="14.11"),
    ],
)
def test_postgresql_probe_derives_pg_version_from_driver_capabilities(server_version, expected):
    # given
    engine = pg_engine()
    # when
    info = engine.probe(fake_driver(server_version))
    # then
    assert info == PostgreSQLServerInfo(pg_version=expected)


def test_postgresql_inject_installs_a_compile_wrapper_with_the_probed_version(alias):
    # given
    engine = pg_engine()
    # when
    engine.inject(alias, dict(PG_SETTINGS), PostgreSQLServerInfo(pg_version=170002))
    # then
    wrapper = injected_wrapper(alias)
    forget_wrapper(alias)
    assert wrapper.pg_version == 170002


def test_postgresql_prepare_returns_a_zero_argument_driver_factory_without_opening_anything():
    # given
    engine = pg_engine()
    # when
    factory = engine.prepare("default", dict(PG_SETTINGS), use_tz=True, time_zone="UTC")
    # then
    assert callable(factory)
