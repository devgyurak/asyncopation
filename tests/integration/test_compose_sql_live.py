"""The connection-less `compose_sql` equals Django's live one on PostgreSQL."""

import pytest

from harness.settings import VENDOR
from tests.support.compose_cases import COMPOSE_CASES, DecimalChoice, Spelled
from tests.support.compose_sql import (
    compose_compile_only,
    compose_error,
    compose_live,
    live_error_name,
)

pytestmark = pytest.mark.skipif(VENDOR != "postgresql", reason="compile-only wrapper: PostgreSQL")


@pytest.mark.parametrize(("sql", "params", "expected"), COMPOSE_CASES)
def test_compose_sql_equals_the_live_connection_result(readonly_db, sql, params, expected):
    # when
    outcome = (compose_compile_only("default", sql, params), compose_live(sql, params))
    # then
    assert outcome == (expected, expected)


@pytest.mark.parametrize(
    "sql_params",
    [
        pytest.param(("%s", ("\ud800",)), id="lone-surrogate"),
        pytest.param(("%s", (chr(0xD83D) + chr(0xDE00),)), id="pair-like-surrogates"),
        pytest.param(("\ud800", None), id="surrogate-in-sql-without-params"),
        pytest.param(("%s \ud800", (1,)), id="surrogate-in-sql-with-params"),
    ],
)
def test_compose_sql_fails_like_the_live_connection_for_a_lone_surrogate(readonly_db, sql_params):
    # given
    sql, params = sql_params
    # when
    names = (compose_error("default", sql, params, "")[0], live_error_name(sql, params))
    # then
    assert names == ("UnicodeEncodeError", "UnicodeEncodeError")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(Spelled("2.50"), id="decimal-with-custom-str"),
        pytest.param(DecimalChoice.A, id="decimal-choices"),
    ],
)
def test_a_decimal_subclass_is_rejected_where_the_live_connection_renders_its_str(
    readonly_db, value
):
    # when
    outcome = (
        compose_error("default", "%s", (value,), "only exact built-in types"),
        compose_live("%s", (value,)),
    )
    # then
    assert outcome == (("TypeError", True), str(value))


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        pytest.param("%s", ("\ud800", 1), id="too-many-before-the-value"),
        pytest.param("%s %s", ("\ud800",), id="too-few-before-the-value"),
    ],
)
def test_the_count_is_checked_before_the_value_as_on_the_live_connection(readonly_db, sql, params):
    # when
    names = (compose_error("default", sql, params, "")[0], live_error_name(sql, params))
    # then
    assert names == ("ValueError", "ProgrammingError")
