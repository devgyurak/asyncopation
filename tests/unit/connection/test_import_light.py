"""Importing the connection layer must not import any driver package (psycopg, asyncmy)."""

import pytest

from tests.support.lifecycle_scripts import MYSQL_FAKE_OPEN, import_script, run_isolated


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("import asyncopation", id="package"),
        pytest.param("import asyncopation.connection", id="connection"),
        pytest.param("import asyncopation.connection.engines", id="engines"),
        pytest.param("import asyncopation.connection.registry", id="registry"),
        pytest.param("import asyncopation.connection.asgi", id="asgi"),
        pytest.param("import asyncopation; asyncopation.setup; asyncopation.close", id="lazy-api"),
        pytest.param(
            "import asyncopation.connection as c; c.setup; c.close; c.configure", id="reexports"
        ),
    ],
)
def test_import_does_not_load_a_driver_package(statement):
    # when
    loaded = run_isolated(import_script(statement))
    # then
    assert loaded == "[]"


def test_opening_a_mysql_fake_engine_does_not_load_psycopg():
    # when
    loaded = run_isolated(MYSQL_FAKE_OPEN)
    # then
    assert loaded == "[]"
