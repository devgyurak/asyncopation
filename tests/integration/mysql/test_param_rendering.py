"""Sequence and binary parameters render like mysqlclient, also under NO_BACKSLASH_ESCAPES."""

from datetime import timedelta

import pytest

from tests.support.mysql_binary import (
    NO_BACKSLASH_ESCAPES,
    WEIRD_TEXT,
    Weird,
    exact_type_cases,
    nested_text_choice_params,
)
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_reference import compare_outcomes
from tests.support.mysql_render import render_mismatches

pytestmark = MYSQL_FAMILY

NO_BACKSLASH_MODES = pytest.mark.parametrize(
    "options",
    [NO_BACKSLASH_ESCAPES, {"sql_mode": "TRADITIONAL,NO_BACKSLASH_ESCAPES"}],
    ids=["init-command", "options-sql-mode"],
)

ANY_MODE = pytest.mark.parametrize(
    "options",
    [{}, NO_BACKSLASH_ESCAPES, {"sql_mode": "TRADITIONAL,NO_BACKSLASH_ESCAPES"}],
    ids=["default-sql-mode", "init-command", "options-sql-mode"],
)

IN_QUERY = "SELECT COUNT(*) FROM (SELECT 1 a UNION SELECT 2) t WHERE 'zzz' IN %s"
INJECTION = "x') OR 1=1 -- "


@NO_BACKSLASH_MODES
@pytest.mark.parametrize(
    "params",
    [
        pytest.param([(INJECTION,)], id="tuple-of-str"),
        pytest.param([[INJECTION, "y'"]], id="list-of-str"),
        pytest.param([(INJECTION.encode(),)], id="tuple-of-bytes"),
        pytest.param([("a\\", "b\\'")], id="backslashes"),
        pytest.param([(1, 2.5, None)], id="numbers-and-none"),
    ],
)
async def test_nested_values_in_a_sequence_parameter_equal_djangos_under_no_backslash_escapes(
    readonly_db, options, params
):
    # when
    result = await compare_outcomes(IN_QUERY, params, options=options)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome[0] == "rows"


@NO_BACKSLASH_MODES
async def test_an_injection_attempt_in_a_sequence_parameter_matches_no_row(readonly_db, options):
    # when
    result = await compare_outcomes(IN_QUERY, [(INJECTION,)], options=options)
    # then
    assert result.async_outcome == ("rows", [(("int", "0"),)])


@NO_BACKSLASH_MODES
async def test_a_mode_option_still_returns_the_same_session_sql_mode(readonly_db, options):
    # when
    result = await compare_outcomes(
        "SELECT @@sql_mode LIKE '%NO_BACKSLASH_ESCAPES%'", options=options
    )
    # then
    assert result.async_outcome == result.sync_outcome == ("rows", [(("int", "1"),)])


@pytest.mark.parametrize(
    "params",
    [
        pytest.param([[1, 2]], id="list"),
        pytest.param([(1, 2)], id="tuple"),
        pytest.param([["a'b", "c"]], id="list-of-str"),
        pytest.param([[]], id="empty-list"),
    ],
)
async def test_sequence_parameters_equal_djangos_in_the_default_mode(readonly_db, params):
    # when
    result = await compare_outcomes("SELECT 1 IN %s", params)
    # then
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"a"}, id="set-of-str"),
        pytest.param(frozenset({"b'"}), id="frozenset-quoted-str"),
        pytest.param(set(), id="empty-set"),
        pytest.param({"a", "b"}, id="set-of-two-str"),
        pytest.param({1, 2}, id="set-of-int"),
        pytest.param({"a", 1}, id="mixed-set"),
        pytest.param(frozenset({b"x"}), id="set-of-bytes"),
    ],
)
async def test_a_set_parameter_equals_djangos_sync_outcome(readonly_db, options, value):
    # when
    result = await compare_outcomes("SELECT %s", [value], options=options)
    # then
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
@pytest.mark.parametrize(
    "value", [{"a"}, frozenset({"b'"}), {"a", "b"}], ids=["one", "quoted", "two"]
)
async def test_a_set_of_strings_is_sent_and_equals_djangos_sync_outcome(
    readonly_db, options, value
):
    # when
    result = await compare_outcomes("SELECT %s", [value], options=options)
    # then
    assert result.async_outcome[0] == "rows"
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
async def test_a_set_of_strings_nested_in_a_tuple_equals_djangos_sync_outcome(readonly_db, options):
    # when
    result = await compare_outcomes("SELECT %s IN %s", ["a", ({"a"},)], options=options)
    # then
    assert result.async_outcome[0] == "rows"
    assert result.async_outcome == result.sync_outcome


@NO_BACKSLASH_MODES
async def test_nested_sequences_with_quotes_render_safely_under_no_backslash_escapes(
    readonly_db, options
):
    # when
    result = await compare_outcomes(IN_QUERY, [(INJECTION, ("a'", [b"b'"]))], options=options)
    # then
    assert result.async_outcome[0] == "error"  # an operand error: the code differs by server
    assert result.async_outcome[1].startswith("django.db.utils.OperationalError(")  # no injection
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
async def test_an_object_of_an_unknown_type_is_rendered_as_a_safe_string_literal(
    readonly_db, options
):
    # when
    result = await compare_outcomes("SELECT %s", [Weird()], options=options)
    # then
    assert result.async_outcome == ("rows", [(("str", repr(WEIRD_TEXT)),)])
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
@pytest.mark.parametrize("container", [tuple, list], ids=["nested-in-a-tuple", "nested-in-a-list"])
async def test_an_unknown_type_nested_in_a_sequence_stays_inside_its_literal(
    readonly_db, options, container
):
    # when
    result = await compare_outcomes(
        "SELECT %s IN %s", [WEIRD_TEXT, container([Weird()])], options=options
    )
    # then
    assert result.async_outcome == ("rows", [(("int", "1"),)])
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
@pytest.mark.parametrize(("sql", "params"), exact_type_cases())
async def test_subclasses_of_numbers_equal_djangos_sync_outcome(readonly_db, options, sql, params):
    # when
    result = await compare_outcomes(sql, params, options=options)
    # then
    assert result.async_outcome[0] == "rows"
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
async def test_a_timedelta_nested_in_a_tuple_is_the_same_time_value_as_for_django(
    readonly_db, options
):
    # when: the text differs ('24:00:00.000005' against mysqlclient's '1 0:0:0.000005')
    result = await compare_outcomes(
        "SELECT TIME_TO_SEC(%s)", [(timedelta(days=1, microseconds=5),)], options=options
    )
    # then: the value does not
    assert result.async_outcome[0] == "rows"
    assert result.async_outcome == result.sync_outcome


@ANY_MODE
@pytest.mark.parametrize("nested", nested_text_choice_params(), ids=["tuple", "list"])
async def test_a_str_enum_member_nested_in_a_sequence_equals_djangos_sync_outcome(
    readonly_db, options, nested
):
    # when
    result = await compare_outcomes("SELECT %s IN %s", ["va'l", nested], options=options)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome[0] == "rows"


@pytest.mark.parametrize(
    ("options", "hex_mode"),
    [
        pytest.param({}, False, id="default-sql-mode"),
        pytest.param(NO_BACKSLASH_ESCAPES, True, id="init-command"),
        pytest.param({"sql_mode": "TRADITIONAL,NO_BACKSLASH_ESCAPES"}, True, id="options-sql-mode"),
    ],
)
async def test_every_parameter_shape_renders_like_mysqlclient(readonly_db, options, hex_mode):
    # when
    mismatches = await render_mismatches(options, hex_mode=hex_mode)
    # then
    assert mismatches == []


@ANY_MODE
async def test_bytes_make_a_comparison_binary_unlike_mysqlclient(readonly_db, options):
    # when: a documented difference: asyncmy sends `_binary'..'`, mysqlclient a plain literal
    result = await compare_outcomes("SELECT 'ABC' = %s", [b"abc"], options=options)
    # then
    assert result.async_outcome == ("rows", [(("int", "0"),)])
    assert result.sync_outcome == ("rows", [(("int", "1"),)])
