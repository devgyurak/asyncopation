"""`bytes` parameters: hex literals under NO_BACKSLASH_ESCAPES, asyncmy's escaping otherwise."""

import pytest

from tests.support.mysql_binary import (
    BIG_BLOB_SIZE,
    INJECTION,
    NO_BACKSLASH_ESCAPES,
    TRICKY,
    big_blob_stored_length,
    selected_back,
    stored_and_read_back,
    stored_and_read_back_by_mysqlclient,
    stored_by_execute_many,
)
from tests.support.mysql_env import MYSQL_FAMILY

pytestmark = MYSQL_FAMILY

MODES = pytest.mark.parametrize(
    "options", [{}, NO_BACKSLASH_ESCAPES], ids=["default-sql-mode", "no-backslash-escapes"]
)
VALUES = pytest.mark.parametrize(
    "value",
    [TRICKY, b"", INJECTION, b"\\", b"'", b"\x00\xff"],
    ids=["tricky", "empty", "injection", "backslash", "quote", "nul-and-ff"],
)


@MODES
@VALUES
async def test_bytes_round_trip_verbatim(django_db_setup, options, value):
    # when
    result = await selected_back(options, value)
    # then
    assert result == value
    assert type(result) is bytes


@MODES
@VALUES
async def test_stored_bytes_read_back_like_mysqlclient_reads_them(binary_table, options, value):
    # when
    ours = await stored_and_read_back(options, binary_table, value)
    reference = await stored_and_read_back_by_mysqlclient(options, binary_table, value)
    # then
    assert ours == reference
    assert type(ours[0]) is type(reference[0])


@MODES
@pytest.mark.parametrize(
    "value",
    [bytearray(TRICKY), memoryview(TRICKY)],
    ids=["bytearray", "memoryview"],
)
async def test_bytearray_and_memoryview_round_trip_as_bytes(django_db_setup, options, value):
    # when
    result = await selected_back(options, value)
    # then
    assert result == TRICKY


@MODES
async def test_a_named_dict_parameter_round_trips(django_db_setup, options):
    # when
    result = await selected_back(options, TRICKY, as_dict=True)
    # then
    assert result == TRICKY


async def test_a_blob_that_fits_the_packet_limit_only_without_hex_doubling_is_stored(binary_table):
    # when
    length = await big_blob_stored_length(binary_table)
    # then
    assert length == BIG_BLOB_SIZE


async def test_an_injection_attempt_returns_the_bytes_verbatim_under_no_backslash_escapes(
    django_db_setup,
):
    # when
    result = await selected_back(NO_BACKSLASH_ESCAPES, INJECTION)
    # then
    assert result == INJECTION


@MODES
@VALUES
async def test_bytes_are_stored_in_blob_and_varbinary_columns(binary_table, options, value):
    # when
    blob, varbinary = await stored_and_read_back(options, binary_table, value)
    # then
    assert (blob, varbinary) == (value, value)


@MODES
async def test_execute_many_stores_every_bytes_value(binary_table, options):
    # when
    stored = await stored_by_execute_many(options, binary_table, [TRICKY, b"", INJECTION])
    # then
    assert stored == [TRICKY, b"", INJECTION]
