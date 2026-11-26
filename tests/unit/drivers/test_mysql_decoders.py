"""The decoder shim: asyncmy's table plus Django's TIME and mysqlclient's zero dates."""

import pytest

from tests.support.mysql_decoders import (
    added_encoder_names,
    added_non_encoder_keys,
    decoded_by_mysqlclient,
    decoded_by_the_shim,
    differing_type_names,
    keys_missing_from_the_shim,
    time_decoder_is_djangos,
)

MATRIX = [
    pytest.param("DATE", "2024-02-29", id="date"),
    pytest.param("DATE", "0000-00-00", id="zero-date"),
    pytest.param("DATE", "2024-00-00", id="zero-month-and-day"),
    pytest.param("DATETIME", "2024-02-29 13:14:15", id="datetime"),
    pytest.param("DATETIME", "2024-02-29 13:14:15.123456", id="datetime-micro"),
    pytest.param("DATETIME", "0000-00-00 00:00:00", id="zero-datetime"),
    pytest.param("TIMESTAMP", "2024-02-29 13:14:15.654321", id="timestamp"),
    pytest.param("TIMESTAMP", "0000-00-00 00:00:00", id="zero-timestamp"),
    pytest.param("TIME", "12:34:56", id="time"),
    pytest.param("TIME", "12:34:56.123456", id="time-micro"),
    pytest.param("TIME", "00:00:00", id="zero-time"),
    pytest.param("NEWDECIMAL", "12345678901234.123456", id="decimal"),
    pytest.param("NEWDECIMAL", "-0.000001", id="negative-decimal"),
    pytest.param("DECIMAL", "1.50", id="old-decimal"),
    pytest.param("TINY", "-1", id="tiny"),
    pytest.param("SHORT", "-300", id="short"),
    pytest.param("LONG", "123456", id="long"),
    pytest.param("INT24", "-8388608", id="int24"),
    pytest.param("LONGLONG", "18446744073709551615", id="bigint-unsigned"),
    pytest.param("YEAR", "2024", id="year"),
    pytest.param("FLOAT", "1.5", id="float"),
    pytest.param("DOUBLE", "2.718281828459045", id="double"),
]


@pytest.mark.parametrize(("name", "raw"), MATRIX)
def test_a_decoded_value_equals_the_one_mysqlclient_returns_in_value_and_type(name, raw):
    # when
    ours = decoded_by_the_shim(name, raw)
    reference = decoded_by_mysqlclient(name, raw)
    # then
    assert ours == reference


def test_only_the_time_and_date_decoders_differ_from_asyncmys_own_table():
    # when
    names = differing_type_names()
    # then
    assert names == {"TIME", "DATE", "DATETIME", "TIMESTAMP"}


def test_the_only_encoder_the_shim_adds_is_sql_literal():
    # when
    names = added_encoder_names()
    # then
    assert names == {"SqlLiteral"}


def test_the_time_decoder_is_djangos_typecast_time():
    # when
    same = time_decoder_is_djangos()
    # then
    assert same is True


def test_the_shim_keeps_every_decoder_and_encoder_of_asyncmys_table():
    # when
    missing = keys_missing_from_the_shim()
    # then
    assert missing == set()


def test_the_shim_adds_no_key_that_is_not_an_encoder_class():
    # when
    extra = added_non_encoder_keys()
    # then
    assert extra == set()
