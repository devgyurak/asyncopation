"""`MySQLServerData` and `parse_server_version`: Django's `mysql_server_data` as an immutable value."""

from dataclasses import FrozenInstanceError

import pytest

from asyncopation._django.mysql import parse_server_version
from asyncopation.drivers.mysql.server_data import MySQLServerData
from tests.support.drivers import set_attribute
from tests.support.mysql_driver_wrappers import field_names
from tests.support.mysql_server import django_parse, server_data

DJANGO_KEYS = {
    "version",
    "sql_mode",
    "default_storage_engine",
    "sql_auto_is_null",
    "lower_case_table_names",
    "has_zoneinfo_database",
}


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        pytest.param("8.4.11", (8, 4, 11), id="mysql"),
        pytest.param("8.0.35-0ubuntu0.22.04.1", (8, 0, 35), id="mysql-distro-suffix"),
        pytest.param("11.8.9-MariaDB-ubu2404", (11, 8, 9), id="mariadb"),
        pytest.param("10.5.29-MariaDB-0+deb11u1", (10, 5, 29), id="mariadb-debian"),
        pytest.param("5.5.5-10.11.2-MariaDB", (5, 5, 5), id="mariadb-replication-prefix"),
        pytest.param("10.11.2a", (10, 11, 2), id="trailing-letter"),
    ],
)
def test_parse_server_version_reads_the_numeric_part_like_django(version, expected):
    # when
    parsed = parse_server_version(version)
    # then
    assert parsed == expected


@pytest.mark.parametrize(
    "version", ["8.4.11", "11.8.9-MariaDB-ubu2404", "5.5.5-10.11.2-MariaDB", "9.0.0-rc1"]
)
def test_parse_server_version_equals_djangos_mysql_version(version):
    # when
    parsed = parse_server_version(version)
    # then
    assert parsed == django_parse(version)


@pytest.mark.parametrize("version", ["", "garbage", "8.4"])
def test_parse_server_version_rejects_a_string_without_a_three_part_version(version):
    # when / then
    with pytest.raises(Exception, match="MySQL version"):
        parse_server_version(version)


def test_server_data_has_exactly_djangos_six_keys():
    # when
    names = field_names(MySQLServerData)
    # then
    assert names == DJANGO_KEYS


def test_server_data_is_frozen():
    # given
    data = server_data()
    # when / then
    with pytest.raises(FrozenInstanceError):
        set_attribute(data, "version", "9.9.9")


def test_server_data_compares_and_hashes_by_value():
    # when
    first, second = server_data(), server_data()
    # then
    assert first == second
    assert hash(first) == hash(second)


@pytest.mark.parametrize(
    ("field", "kind"),
    [
        ("version", str),
        ("sql_mode", str),
        ("default_storage_engine", str),
        ("sql_auto_is_null", bool),
        ("lower_case_table_names", bool),
        ("has_zoneinfo_database", bool),
    ],
)
def test_server_data_fields_have_djangos_types(field, kind):
    # when
    value = getattr(server_data(), field)
    # then
    assert type(value) is kind
