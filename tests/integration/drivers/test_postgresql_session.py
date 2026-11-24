"""Connection setup mirrors Django's sync backend (OPTIONS handling, session state)."""

import psycopg
import pytest

from tests.support.drivers import (
    POSTGRES_ONLY,
    TzCase,
    isolation_in_transaction,
    open_driver,
    prepared_probe_count,
    query_text_seen_by_server,
    session_setting,
    temporary_role,
    uses_placeholders,
)

pytestmark = POSTGRES_ONLY


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(TzCase(True, None, "UTC"), id="utc"),
        pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="db-seoul"),
        pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
    ],
)
async def test_session_time_zone_is_connection_time_zone(django_db_setup, case):
    # given
    async with open_driver(case=case) as driver, driver.connection() as conn:
        # when
        zone = await session_setting(conn, "TimeZone")
    # then
    assert zone == case.time_zone


async def test_client_encoding_option_is_forced_to_utf8(django_db_setup):
    # given
    async with (
        open_driver(options={"client_encoding": "latin1"}) as driver,
        driver.connection() as conn,
    ):
        # when
        encoding = await session_setting(conn, "client_encoding")
    # then
    assert encoding == "UTF8"


async def test_assume_role_option_sets_current_user(django_db_setup):
    # given
    async with (
        temporary_role("asyncopation_test_role") as role,
        open_driver(options={"assume_role": role}) as driver,
        driver.connection() as conn,
    ):
        # when
        rows = await conn.fetch_all("SELECT current_user", None)
    # then
    assert rows == [("asyncopation_test_role",)]


async def test_isolation_level_option_applies_to_transactions(django_db_setup):
    # given
    options = {"isolation_level": psycopg.IsolationLevel.SERIALIZABLE}
    async with open_driver(options=options) as driver, driver.connection() as conn:
        await conn.begin()
        # when
        level = await session_setting(conn, "transaction_isolation")
        await conn.rollback()
    # then
    assert level == "serializable"


async def test_default_isolation_level_is_read_committed(django_db_setup, driver):
    # given
    async with driver.connection() as conn:
        await conn.begin()
        # when
        level = await session_setting(conn, "transaction_isolation")
        await conn.rollback()
    # then
    assert level == "read committed"


async def test_server_side_binding_decides_how_parameters_reach_the_server(driver, binding):
    # given
    async with driver.connection() as conn:
        # when
        text = await query_text_seen_by_server(conn)
    # then
    assert uses_placeholders(text) is binding.server_side


async def test_prepare_threshold_zero_prepares_statements_at_first_use(django_db_setup):
    # given
    options = {"server_side_binding": True, "prepare_threshold": 0}
    async with open_driver(options=options) as driver, driver.connection() as conn:
        # when
        prepared = await prepared_probe_count(conn)
    # then
    assert prepared == 1


async def test_default_prepare_threshold_does_not_prepare_on_first_use(django_db_setup):
    # given
    async with (
        open_driver(options={"server_side_binding": True}) as driver,
        driver.connection() as conn,
    ):
        # when
        prepared = await prepared_probe_count(conn)
    # then
    assert prepared == 0


async def test_server_default_isolation_applies_without_isolation_level_option(django_db_setup):
    # given
    options = {"options": "-c default_transaction_isolation=serializable"}
    # when
    level = await isolation_in_transaction(options)
    # then
    assert level == "serializable"


async def test_isolation_level_option_overrides_the_server_default(django_db_setup):
    # given
    options = {
        "options": "-c default_transaction_isolation=serializable",
        "isolation_level": psycopg.IsolationLevel.REPEATABLE_READ,
    }
    # when
    level = await isolation_in_transaction(options)
    # then
    assert level == "repeatable read"


async def test_explicit_read_committed_overrides_a_stricter_server_default(django_db_setup):
    # given
    options = {
        "options": "-c default_transaction_isolation=serializable",
        "isolation_level": psycopg.IsolationLevel.READ_COMMITTED,
    }
    # when
    level = await isolation_in_transaction(options)
    # then
    assert level == "read committed"
