"""A typed table for value/type comparisons between the async driver and mysqlclient.

Rows cover regular values, NULLs and zero dates (inserted with `sql_mode = ''`, the only way the
server accepts them).
"""

from __future__ import annotations

from tests.support.mysql_env import RUN_ID, Row, admin_script, open_my_driver
from tests.support.mysql_reference import (
    RowsComparison,
    normalise,
    sync_rows,
)

TYPED_TABLE = f"asyncopation_typed_{RUN_ID}"

TYPED_COLUMNS = (
    "c_datetime",
    "c_timestamp",
    "c_date",
    "c_time",
    "c_decimal",
    "c_json",
    "c_blob",
    "c_varbinary",
    "c_bit1",
    "c_bit8",
    "c_tinyint1",
    "c_year",
    "c_float",
    "c_double",
    "c_bigint_unsigned",
    "c_text",
)

_DDL = f"""
CREATE TABLE {TYPED_TABLE} (
    id INT PRIMARY KEY,
    c_datetime DATETIME(6) NULL,
    c_timestamp TIMESTAMP(6) NULL DEFAULT NULL,
    c_date DATE NULL,
    c_time TIME(6) NULL,
    c_decimal DECIMAL(20, 6) NULL,
    c_json JSON NULL,
    c_blob BLOB NULL,
    c_varbinary VARBINARY(16) NULL,
    c_bit1 BIT(1) NULL,
    c_bit8 BIT(8) NULL,
    c_tinyint1 TINYINT(1) NULL,
    c_year YEAR NULL,
    c_float FLOAT NULL,
    c_double DOUBLE NULL,
    c_bigint_unsigned BIGINT UNSIGNED NULL,
    c_text TEXT CHARACTER SET utf8mb4 NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_ROWS = (
    # regular values
    f"""INSERT INTO {TYPED_TABLE} VALUES (1, '2024-02-29 13:14:15.123456',
        '2024-02-29 13:14:15.654321', '2024-02-29', '12:34:56.123456', '12345678901234.123456',
        '{{"a": [1, 2, {{"b": null}}]}}', 0x00FF10, 0x0102, b'1', b'10101010', 1, 2024, 1.5,
        2.718281828459045, 18446744073709551615, 'héllo 한글 😀')""",
    # extremes
    f"""INSERT INTO {TYPED_TABLE} VALUES (2, '1000-01-01 00:00:00.000001',
        '2038-01-19 03:14:07.000000', '9999-12-31', '23:59:59.999999', '-0.000001',
        '[]', '', '', b'0', b'00000000', -1, 2155, -3.4e38, -1.7976931348623157e308,
        0, '')""",
    # every column NULL
    f"INSERT INTO {TYPED_TABLE} (id) VALUES (3)",
    # zero dates
    f"""INSERT INTO {TYPED_TABLE} (id, c_datetime, c_timestamp, c_date) VALUES (4,
        '0000-00-00 00:00:00', '0000-00-00 00:00:00', '0000-00-00')""",
    # the smallest time and a zero time
    f"INSERT INTO {TYPED_TABLE} (id, c_time) VALUES (5, '00:00:00.000001')",
    f"INSERT INTO {TYPED_TABLE} (id, c_time) VALUES (6, '00:00:00')",
)

ZERO_DATE_ROW_ID = 4


async def create_typed_table() -> str:
    await admin_script([f"DROP TABLE IF EXISTS {TYPED_TABLE}"])
    await admin_script(["SET SESSION sql_mode = ''", _DDL])
    for statement in _ROWS:
        await admin_script(["SET SESSION sql_mode = ''", statement])
    return TYPED_TABLE


async def drop_typed_table() -> None:
    await admin_script([f"DROP TABLE IF EXISTS {TYPED_TABLE}"])


def column_sql(column: str) -> str:
    return f"SELECT {column} FROM {TYPED_TABLE} ORDER BY id"


async def compare_column(column: str) -> RowsComparison:
    """One column of every typed row: async driver vs mysqlclient (values and types)."""
    sql = column_sql(column)
    async with open_my_driver() as driver, driver.connection() as conn:
        async_rows = await conn.fetch_all(sql, None)
    reference = await sync_rows(None, sql)
    return RowsComparison(normalise(async_rows), normalise(reference))


async def compare_all_columns() -> RowsComparison:
    columns = ", ".join(TYPED_COLUMNS)
    sql = f"SELECT id, {columns} FROM {TYPED_TABLE} ORDER BY id"
    async with open_my_driver() as driver, driver.connection() as conn:
        async_rows = await conn.fetch_all(sql, None)
    reference = await sync_rows(None, sql)
    return RowsComparison(normalise(async_rows), normalise(reference))


async def zero_date_row() -> Row:
    """The zero-date row as the async driver decodes it."""
    sql = f"SELECT c_datetime, c_timestamp, c_date FROM {TYPED_TABLE} WHERE id = {ZERO_DATE_ROW_ID}"
    async with open_my_driver() as driver, driver.connection() as conn:
        rows = await conn.fetch_all(sql, None)
    return rows[0]
