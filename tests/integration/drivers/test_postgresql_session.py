"""Connection setup mirrors Django's sync backend (OPTIONS handling, session state)."""

import psycopg
import pytest

from tests.support.drivers import (
    POSTGRES_ONLY,
    TzCase,
    compare_session_value,
    isolation_in_transaction,
    open_driver,
    prepared_probe_count,
    query_text_seen_by_server,
    session_setting,
    temporary_role,
    uses_placeholders,
)

pytestmark = POSTGRES_ONLY

SERIALIZABLE_DEFAULT = {"options": "-c default_transaction_isolation=serializable"}


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(TzCase(True, None, "UTC"), id="utc"),
        pytest.param(TzCase(True, None, "Asia/Seoul"), id="settings-seoul-ignored"),
        pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="db-seoul"),
        pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
    ],
)
async def test_session_time_zone_matches_sync_backend(readonly_db, binding, case):
    # when
    result = await compare_session_value(case, binding.options, "SHOW TimeZone")
    # then
    assert result.async_value == result.sync_value


async def test_session_time_zone_ignores_settings_zone_when_use_tz_is_on(readonly_db):
    # when
    result = await compare_session_value(TzCase(True, None, "Asia/Seoul"), None, "SHOW TimeZone")
    # then
    assert result.async_value == "UTC"


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


async def test_assume_role_option_matches_sync_backend(readonly_db, binding):
    # given
    async with temporary_role("asyncopation_test_role") as role:
        options = {**binding.options, "assume_role": role}
        # when
        result = await compare_session_value(
            TzCase(True, None, "UTC"), options, "SELECT current_user"
        )
    # then
    assert result.async_value == result.sync_value == "asyncopation_test_role"


@pytest.mark.parametrize(
    "isolation",
    [
        pytest.param(psycopg.IsolationLevel.SERIALIZABLE, id="serializable"),
        pytest.param(psycopg.IsolationLevel.REPEATABLE_READ, id="repeatable-read"),
        pytest.param(psycopg.IsolationLevel.READ_COMMITTED, id="read-committed"),
    ],
)
async def test_isolation_level_option_matches_sync_backend(readonly_db, binding, isolation):
    # given
    options = {**binding.options, "isolation_level": isolation}
    # when
    result = await compare_session_value(
        TzCase(True, None, "UTC"), options, "SHOW transaction_isolation", transactional=True
    )
    # then
    assert result.async_value == result.sync_value


async def test_default_isolation_level_matches_sync_backend(readonly_db, binding):
    # when
    result = await compare_session_value(
        TzCase(True, None, "UTC"),
        binding.options,
        "SHOW transaction_isolation",
        transactional=True,
    )
    # then
    assert result.async_value == result.sync_value == "read committed"


async def test_server_default_isolation_applies_without_isolation_level_option(readonly_db):
    # when
    result = await compare_session_value(
        TzCase(True, None, "UTC"),
        SERIALIZABLE_DEFAULT,
        "SHOW transaction_isolation",
        transactional=True,
    )
    # then
    assert result.async_value == result.sync_value == "serializable"


async def test_isolation_level_option_overrides_the_server_default(django_db_setup):
    # given
    options = {**SERIALIZABLE_DEFAULT, "isolation_level": psycopg.IsolationLevel.REPEATABLE_READ}
    # when
    level = await isolation_in_transaction(options)
    # then
    assert level == "repeatable read"


async def test_explicit_read_committed_overrides_a_stricter_server_default(django_db_setup):
    # given
    options = {**SERIALIZABLE_DEFAULT, "isolation_level": psycopg.IsolationLevel.READ_COMMITTED}
    # when
    level = await isolation_in_transaction(options)
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
