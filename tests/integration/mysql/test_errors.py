"""Django exception mapping on a real server."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, ProgrammingError

from tests.support.mysql_env import MYSQL_FAMILY, MYSQL_ONLY, build_my_driver
from tests.support.mysql_scenarios import error_of
from tests.support.mysql_simulate import force_full_auth_without_cryptography

pytestmark = MYSQL_FAMILY


@pytest.mark.parametrize(
    ("sql", "params", "expected"),
    [
        pytest.param("INSERT INTO {t} (name) VALUES (%s)", ("dup",), IntegrityError, id="unique"),
        pytest.param("INSERT INTO {t} (name) VALUES (NULL)", None, IntegrityError, id="not-null"),
        pytest.param(
            "INSERT INTO {t} (name, qty) VALUES ('neg', -1)", None, IntegrityError, id="check"
        ),
        pytest.param(
            "SELECT CAST(1 AS UNSIGNED) - 2", None, IntegrityError, id="bigint-unsigned-range"
        ),
        pytest.param("SELECT * FROM no_such_table_xyz", None, ProgrammingError, id="no-table"),
        pytest.param("SELEC 1", None, ProgrammingError, id="syntax"),
    ],
)
async def test_server_errors_map_to_django_classes(driver, scratch, sql, params, expected):
    # given
    async with driver.connection() as conn:
        await conn.execute(f"INSERT INTO {scratch} (name) VALUES ('dup')", None)
        # when
        error = await error_of(conn.execute(sql.format(t=scratch), params))
    # then
    assert type(error) is expected


async def test_a_failed_statement_keeps_the_connection_usable(driver, scratch):
    # given
    async with driver.connection() as conn:
        await conn.execute(f"INSERT INTO {scratch} (name) VALUES ('dup')", None)
        await error_of(conn.execute(f"INSERT INTO {scratch} (name) VALUES ('dup')", None))
        # when
        rows = await conn.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


@MYSQL_ONLY
async def test_a_missing_cryptography_package_is_an_improperly_configured_error(
    django_db_setup, monkeypatch
):
    # given
    force_full_auth_without_cryptography(monkeypatch)
    # when
    error = await error_of(build_my_driver().open())
    # then
    assert type(error) is ImproperlyConfigured
    assert "cryptography" in str(error)
