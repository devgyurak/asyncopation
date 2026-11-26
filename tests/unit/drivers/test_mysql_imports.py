"""Import hygiene: which driver packages do the MySQL code paths load (clean interpreter)."""

import pytest

from tests.support.mysql_imports import (
    COMPOSED_WRAPPER_COMPILES,
    MYSQL_ENGINE,
    REAL_WRAPPER_IS_TOUCHED,
    loaded_driver_modules,
)


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("import asyncopation.connection", id="connection"),
        pytest.param("import asyncopation.connection.engines", id="engines"),
        pytest.param("import asyncopation.compile.server_info", id="server-info"),
        pytest.param("import asyncopation._django.mysql", id="django-boundary"),
        pytest.param("import asyncopation.compile.mysql", id="compile-wrapper"),
        pytest.param(
            "from asyncopation.connection.engines import FACTORIES; "
            "assert 'django.db.backends.mysql' in FACTORIES",
            id="engine-registered-not-built",
        ),
    ],
)
def test_import_loads_neither_mysqldb_nor_asyncmy(statement):
    # when
    result = loaded_driver_modules(statement)
    # then
    assert result == (0, "[]")


def test_compiling_with_the_composed_wrapper_never_loads_mysqldb():
    # when
    result = loaded_driver_modules(COMPOSED_WRAPPER_COMPILES)
    # then
    assert result == (0, "[]")


def test_a_default_mysql_engine_does_load_mysqldb_through_django_itself():
    # when
    exit_code, modules = loaded_driver_modules(REAL_WRAPPER_IS_TOUCHED, MYSQL_ENGINE)
    # then
    assert exit_code == 0
    assert "MySQLdb" in modules


def test_importing_the_asyncmy_driver_loads_asyncmy_but_never_mysqldb_or_psycopg():
    # when
    exit_code, modules = loaded_driver_modules("import asyncopation.drivers.mysql")
    # then
    assert exit_code == 0
    assert "asyncmy" in modules
    assert "MySQLdb" not in modules
    assert "psycopg" not in modules


def test_building_the_mysql_engine_driver_loads_asyncmy_but_never_mysqldb_or_psycopg():
    # when
    exit_code, modules = loaded_driver_modules(
        "from asyncopation.connection.engines import FACTORIES\n"
        "engine = FACTORIES['django.db.backends.mysql']()\n"
        "engine.prepare('default', {**__import__('django').conf.settings.DATABASES['default'], "
        "'HOST': '', 'PORT': '', 'USER': '', 'PASSWORD': '', 'OPTIONS': {}}, "
        "use_tz=True, time_zone='UTC')()"
    )
    # then
    assert exit_code == 0
    assert "asyncmy" in modules
    assert "MySQLdb" not in modules
    assert "psycopg" not in modules
