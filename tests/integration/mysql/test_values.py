"""Row-by-row value comparison with mysqlclient (Django conversions): values AND types."""

import pytest

from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_values import (
    TYPED_COLUMNS,
    compare_all_columns,
    compare_column,
    zero_date_row,
)

pytestmark = MYSQL_FAMILY


@pytest.mark.parametrize("column", TYPED_COLUMNS)
async def test_a_column_decodes_like_mysqlclient(typed, column):
    # when
    result = await compare_column(column)
    # then
    assert result.async_rows == result.sync_rows


async def test_a_whole_typed_row_decodes_like_mysqlclient(typed):
    # when
    result = await compare_all_columns()
    # then
    assert result.async_rows == result.sync_rows


async def test_zero_dates_decode_as_none(typed):
    # when
    row = await zero_date_row()
    # then
    assert row == (None, None, None)


async def test_a_time_column_decodes_as_a_datetime_time(typed):
    # when
    result = await compare_column("c_time")
    # then
    assert result.async_rows[0] == (("time", "datetime.time(12, 34, 56, 123456)"),)
