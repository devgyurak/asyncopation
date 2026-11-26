"""Session state of pooled connections: init_command composition, isolation level, time zone."""

import pytest

from tests.support.mysql_env import MYSQL_FAMILY, open_my_driver
from tests.support.mysql_reference import compare_outcomes, compare_transaction_isolation
from tests.support.mysql_scenarios import (
    rows_of,
    session_values,
    variable_on_two_connections,
)

pytestmark = MYSQL_FAMILY

ONE_CONNECTION = {"min_size": 1, "max_size": 1}


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("SET @asyncopation_probe = 7", id="plain"),
        pytest.param("SET @asyncopation_probe = 7;", id="trailing-semicolon"),
        pytest.param("SET @asyncopation_probe = 7;  \n", id="trailing-semicolon-and-space"),
        pytest.param("SET @asyncopation_probe = 7 -- x", id="trailing-dash-comment"),
        pytest.param("SET @asyncopation_probe = 7 # x", id="trailing-hash-comment"),
    ],
)
async def test_the_user_init_command_runs_on_every_pooled_connection(django_db_setup, command):
    # when
    values = await variable_on_two_connections({"init_command": command}, "asyncopation_probe")
    # then
    assert values == [7, 7]


@pytest.mark.parametrize(
    "command",
    ["SET @a = 1;", "SET @a = 1 -- x", "SET @a = 1 # x"],
    ids=["semicolon", "dash-comment", "hash-comment"],
)
async def test_the_composed_init_command_still_applies_the_default_isolation(
    django_db_setup, command
):
    # given
    options = {"init_command": command}
    async with open_my_driver(options=options, pool=ONE_CONNECTION) as driver:
        # when
        values = await session_values(driver)
    # then
    assert values[0] == "READ-COMMITTED"


async def test_the_next_query_on_a_connection_works_after_the_composed_init_command(
    django_db_setup,
):
    # given
    options = {"init_command": "SET @asyncopation_probe = 7;"}
    async with open_my_driver(options=options, pool=ONE_CONNECTION) as driver:
        # when
        rows = await rows_of(driver, "SELECT 1, 'x'")
    # then
    assert rows == [(1, "x")]


async def test_the_default_isolation_level_is_read_committed(driver):
    # when
    values = await session_values(driver)
    # then
    assert values[0] == "READ-COMMITTED"


async def test_an_isolation_level_option_applies_to_pooled_connections(serializable_driver):
    # when
    values = await session_values(serializable_driver)
    # then
    assert values[0] == "SERIALIZABLE"


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="default"),
        pytest.param({"isolation_level": "serializable"}, id="serializable"),
        pytest.param({"isolation_level": "repeatable read"}, id="repeatable-read"),
        pytest.param({"isolation_level": "READ UNCOMMITTED"}, id="upper-case"),
        pytest.param({"isolation_level": None}, id="server-default"),
    ],
)
async def test_the_isolation_level_equals_the_one_django_sets(readonly_db, options):
    # when
    result = await compare_transaction_isolation(options)
    # then
    assert result.async_value == result.sync_value


async def test_the_session_time_zone_is_the_servers_not_changed_by_the_driver(driver):
    # when
    values = await session_values(driver)
    # then
    assert values[2] == values[3]


async def test_connections_are_in_autocommit_mode(driver):
    # when
    values = await session_values(driver)
    # then
    assert values[1] == 1


async def test_sql_auto_is_null_is_switched_off_even_when_the_users_init_command_enables_it(
    readonly_db,
):
    # when
    result = await compare_outcomes(
        "SELECT @@sql_auto_is_null", options={"init_command": "SET sql_auto_is_null = 1"}
    )
    # then
    assert result.async_outcome == result.sync_outcome == ("rows", [(("int", "0"),)])


async def test_several_modes_in_the_sql_mode_option_open_and_apply(readonly_db):
    # when
    result = await compare_outcomes(
        "SELECT @@sql_mode LIKE '%NO_BACKSLASH_ESCAPES%' AND @@sql_mode LIKE '%STRICT_TRANS_TABLES%'",
        options={"sql_mode": "TRADITIONAL,NO_BACKSLASH_ESCAPES"},
    )
    # then
    assert result.async_outcome == result.sync_outcome == ("rows", [(("int", "1"),)])


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(
            {"init_command": "SET sql_mode='ANSI'", "sql_mode": "TRADITIONAL"}, id="option-wins"
        ),
        pytest.param({"init_command": "SET sql_mode='ANSI'", "sql_mode": ""}, id="empty-ignored"),
        pytest.param({"sql_mode": ""}, id="empty-alone"),
        pytest.param({"sql_mode": "ANSI,NO_BACKSLASH_ESCAPES"}, id="several"),
    ],
)
async def test_the_session_sql_mode_equals_djangos_for_the_same_options(readonly_db, options):
    # when
    result = await compare_outcomes("SELECT @@sql_mode", options=options)
    # then
    assert result.async_outcome[0] == "rows"
    assert result.async_outcome == result.sync_outcome
