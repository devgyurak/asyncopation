"""Value/type normalisation: the async driver returns what Django's sync cursor returns."""

import pytest

from tests.support.drivers import (
    POSTGRES_ONLY,
    TzCase,
    all_plain_tuples,
    compare_extra_round_trip,
    compare_outcomes,
    compare_round_trip,
    compare_with_sync,
    field_zoo_sql,
    open_driver,
)

pytestmark = POSTGRES_ONLY

SEOUL_CASES = [
    pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="use-tz-db-seoul"),
    pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
]
# Cases in which the connection time zone is UTC: every FieldZoo row is representable.
UTC_CASES = [
    pytest.param(TzCase(True, None, "UTC"), id="use-tz-utc"),
    pytest.param(TzCase(True, None, "Asia/Seoul"), id="use-tz-settings-seoul-ignored"),
    pytest.param(TzCase(False, None, "UTC"), id="no-tz-utc"),
]
ALL_CASES = [*UTC_CASES, *SEOUL_CASES]


@pytest.mark.parametrize("case", UTC_CASES)
async def test_field_zoo_rows_match_sync_cursor_in_utc(readonly_db, binding, case):
    # given
    sql = field_zoo_sql()
    # when
    result = await compare_with_sync(sql, case=case, options=binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.sync_rows) == 4


@pytest.mark.parametrize("case", SEOUL_CASES)
async def test_field_zoo_rows_match_sync_cursor_outside_utc_without_the_year_9999_row(
    readonly_db, binding, case
):
    # given: the row with datetime 9999-12-31T23:59:59 UTC overflows in UTC+9, see next test
    sql = field_zoo_sql(exclude_char="max")
    # when
    result = await compare_with_sync(sql, case=case, options=binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.sync_rows) == 3


@pytest.mark.parametrize("case", SEOUL_CASES)
async def test_year_9999_overflow_outside_utc_fails_like_sync_cursor(readonly_db, binding, case):
    # given
    sql = field_zoo_sql()
    # when
    result = await compare_outcomes(sql, case=case, options=binding.options)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome[0] == "error"


@pytest.mark.parametrize("case", ALL_CASES)
async def test_parameters_round_trip_like_sync_cursor(readonly_db, binding, case):
    # when
    result = await compare_round_trip(case, binding.options)
    # then
    assert result.async_rows == result.sync_rows


async def test_json_inet_float_and_array_parameters_round_trip_like_sync_cursor(
    readonly_db, binding
):
    # when
    result = await compare_extra_round_trip(binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.async_rows) == 1


async def test_rows_are_plain_tuples(readonly_db, binding):
    # given
    async with open_driver(options=binding.options) as driver, driver.connection() as conn:
        # when
        rows = await conn.fetch_all(field_zoo_sql(), None)
    # then
    assert all_plain_tuples(rows)
    assert len(rows) == 4


async def test_empty_result_is_empty_list(driver):
    # given
    async with driver.connection() as conn:
        # when
        rows = await conn.fetch_all("SELECT 1 WHERE false", None)
    # then
    assert rows == []


async def test_null_values_come_back_as_none(driver):
    # given
    async with driver.connection() as conn:
        # when
        rows = await conn.fetch_all("SELECT NULL::int, %s::text", (None,))
    # then
    assert rows == [(None, None)]


async def test_no_params_leaves_percent_signs_alone(driver):
    # given
    async with driver.connection() as conn:
        # when
        rows = await conn.fetch_all("SELECT '100%'", None)
    # then
    assert rows == [("100%",)]


async def test_empty_params_tuple_behaves_like_sync_cursor(readonly_db, binding):
    # when
    result = await compare_outcomes("SELECT '100%'", (), options=binding.options)
    # then
    assert result.async_outcome == result.sync_outcome
