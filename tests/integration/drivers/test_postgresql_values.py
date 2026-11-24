"""Value/type normalisation: the async driver returns what Django's sync cursor returns."""

import pytest

from tests.support.drivers import (
    POSTGRES_ONLY,
    TzCase,
    all_plain_tuples,
    compare_round_trip,
    compare_with_sync,
    field_zoo_row_count,
    field_zoo_sql,
    open_driver,
)

pytestmark = POSTGRES_ONLY

TZ_CASES = [
    pytest.param(TzCase(True, None, "UTC"), id="use-tz-utc"),
    pytest.param(TzCase(True, "Asia/Seoul", "UTC"), id="use-tz-db-seoul"),
    pytest.param(TzCase(False, None, "Asia/Seoul"), id="no-tz-seoul"),
    pytest.param(TzCase(False, None, "UTC"), id="no-tz-utc"),
]


@pytest.mark.parametrize("case", TZ_CASES)
async def test_field_zoo_rows_match_sync_cursor(readonly_db, binding, case):
    # given
    sql = field_zoo_sql(case)
    # when
    result = await compare_with_sync(sql, case=case, options=binding.options)
    # then
    assert result.async_rows == result.sync_rows
    assert len(result.sync_rows) == field_zoo_row_count(case)


@pytest.mark.parametrize("case", TZ_CASES)
async def test_parameters_round_trip_like_sync_cursor(readonly_db, binding, case):
    # when
    result = await compare_round_trip(case, binding.options)
    # then
    assert result.async_rows == result.sync_rows


async def test_rows_are_plain_tuples(readonly_db, binding):
    # given
    async with open_driver(options=binding.options) as driver, driver.connection() as conn:
        # when
        rows = await conn.fetch_all(field_zoo_sql(), None)
    # then
    assert all_plain_tuples(rows)
    assert len(rows) == 4


async def test_empty_result_is_empty_list(readonly_db, driver):
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
