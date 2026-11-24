"""The driver test helpers that carry logic deserve their own tests."""

import pytest

from tests.support.drivers import (
    ConnKwargsComparison,
    TzCase,
    field_zoo_sql,
    normalise,
)


def test_differences_lists_only_keys_whose_values_differ():
    # given
    comparison = ConnKwargsComparison({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    # when
    differences = comparison.differences()
    # then
    assert differences == {"b": (2, 3), "c": (None, 4)}


def test_differences_ignores_cursor_factory_and_autocommit():
    # given
    comparison = ConnKwargsComparison(
        {"cursor_factory": int, "autocommit": True}, {"cursor_factory": str}
    )
    # when
    differences = comparison.differences()
    # then
    assert differences == {}


def test_cursor_factories_correspond_when_the_reference_mro_contains_the_sync_class():
    # given
    class ClientCursor: ...

    class Cursor(ClientCursor): ...

    class AsyncClientCursor: ...

    comparison = ConnKwargsComparison(
        {"cursor_factory": AsyncClientCursor}, {"cursor_factory": Cursor}
    )
    # when
    result = comparison.cursor_factories_correspond()
    # then
    assert result is True


def test_normalise_distinguishes_equal_values_of_different_types():
    # when
    ints, floats = normalise([(1,)]), normalise([(1.0,)])
    # then
    assert ints != floats


def test_normalise_turns_memoryview_into_comparable_bytes():
    # when
    left, right = normalise([(memoryview(b"ab"),)]), normalise([(memoryview(b"ab"),)])
    # then
    assert left == right


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(TzCase(True, None, "Asia/Seoul"), "UTC", id="use-tz-defaults-to-utc"),
        pytest.param(TzCase(True, "Asia/Seoul", "UTC"), "Asia/Seoul", id="use-tz-db-zone"),
        pytest.param(TzCase(False, None, "Asia/Seoul"), "Asia/Seoul", id="no-tz-settings-zone"),
        pytest.param(TzCase(False, "Asia/Tokyo", "UTC"), "UTC", id="no-tz-ignores-db-zone"),
    ],
)
def test_tz_case_time_zone_follows_djangos_rules(case, expected):
    # when
    zone = case.time_zone
    # then
    assert zone == expected


def test_field_zoo_sql_orders_by_id_and_quotes_reserved_columns():
    # when
    sql = field_zoo_sql()
    # then
    assert sql.endswith("ORDER BY id")
    assert '"char"' in sql


def test_field_zoo_sql_can_exclude_one_row():
    # when
    sql = field_zoo_sql(exclude_char="max")
    # then
    assert "\"char\" <> 'max'" in sql
