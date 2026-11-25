"""`SqlLiteral` / `encode_literal` and `_prepare_params`: parameters asyncmy cannot escape safely."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import ProgrammingError

from tests.support.mysql_batch import LONE_SURROGATES
from tests.support.mysql_binary import (
    INJECTION,
    QUOTED_TEXT,
    TRICKY,
    WEIRD_TEXT,
    FrozenSubclass,
    NonFiniteFloat,
    Point,
    SurrogateInt,
    SurrogateObject,
    SurrogateText,
    TaggedBytes,
    TupleSubclass,
    Weird,
    conversion_for_sql_literal,
    encode_literal_function,
    encoded,
    expected_subclass_text,
    format_with,
    kinds,
    literal_texts,
    nested_text_choice_params,
    number_subclass_params,
    prepare,
    rendered,
    rendered_numbers,
    wrap_in_dict,
    wrap_in_list,
    wrap_in_set,
    wrap_in_tuple,
    wrap_top,
)


def test_encode_literal_returns_the_text_unchanged():
    # when
    result = encoded("X'6162'")
    # then
    assert result == "X'6162'"


def test_the_shim_registers_encode_literal_for_sql_literal():
    # when
    registered = conversion_for_sql_literal()
    # then
    assert registered is encode_literal_function()


@pytest.mark.parametrize(
    "params",
    [(1, "a", None, b"x"), [1, "a"], {"a": 1, "b": b"x"}, (), {}, None],
    ids=["tuple", "list", "dict", "empty-tuple", "empty-dict", "none"],
)
def test_default_mode_returns_the_same_object_when_nothing_needs_preparing(params):
    # when
    result = prepare(params)
    # then
    assert result is params


@pytest.mark.parametrize("value", [[1, 2], (1, [2]), ("a", 1)], ids=["list", "tuple-list", "tuple"])
def test_default_mode_rebuilds_sequence_values_as_tuples(value):
    # when
    result = prepare((value,))
    # then
    assert kinds(result) == ["tuple"]


def test_default_mode_turns_bytearray_memoryview_and_bytes_subclasses_into_bytes():
    # when
    result = prepare((bytearray(b"a"), memoryview(b"b"), TaggedBytes(b"c"), b"d"))
    # then
    assert kinds(result) == ["bytes"] * 4
    assert result == (b"a", b"b", b"c", b"d")


@pytest.mark.parametrize("value", [{"a"}, frozenset({"a"})], ids=["set", "frozenset"])
def test_default_mode_turns_a_set_of_strings_into_one_string(value):
    # when
    result = prepare((value,))
    # then
    assert result == ("a",)


@pytest.mark.parametrize("value", [{1}, frozenset({"a", 1})], ids=["ints", "mixed"])
def test_a_set_with_non_string_items_raises_type_error_like_mysqlclient(value):
    # when / then
    with pytest.raises(TypeError, match="set elements must be strings"):
        prepare((value,))


@pytest.mark.parametrize("value", [{"a"}, frozenset({"a"})], ids=["set", "frozenset"])
def test_no_backslash_escapes_renders_a_set_of_strings_as_one_literal(value):
    # when
    result = prepare((value,), no_backslash_escapes=True)
    # then
    assert literal_texts(result) == ["<a>"]


def test_default_mode_leaves_plain_bytes_to_asyncmy():
    # given
    params = (TRICKY, 1)
    # when
    result = prepare(params)
    # then
    assert result is params


def test_default_mode_prepares_dict_values_by_key():
    # when
    result = prepare({"a": [1], "b": 2})
    # then
    assert kinds(result) == ["tuple", "int"]
    assert list(result) == ["a", "b"]


def test_preparing_does_not_mutate_the_input():
    # given
    params = ([1, 2], b"x")
    # when
    prepare(params, no_backslash_escapes=True)
    # then
    assert params == ([1, 2], b"x")


@pytest.mark.parametrize(
    "value",
    [TRICKY, INJECTION, bytearray(b"a"), memoryview(b"a"), TaggedBytes(b"a"), b""],
    ids=["tricky", "injection", "bytearray", "memoryview", "subclass", "empty"],
)
def test_no_backslash_escapes_renders_binary_values_as_hex_literals(value):
    # when
    result = prepare((value,), no_backslash_escapes=True)
    # then
    assert kinds(result) == ["SqlLiteral"]
    assert literal_texts(result) == [f"X'{bytes(value).hex()}'"]


def test_no_backslash_escapes_renders_a_sequence_value_with_the_connections_own_literals():
    # when
    result = prepare([("x') OR 1=1 -- ", 5)], no_backslash_escapes=True)
    # then
    assert kinds(result) == ["SqlLiteral"]
    assert literal_texts(result) == ["(<x') OR 1=1 -- >,<5>)"]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        pytest.param(["a", b"b"], "(<a>,X'62')", id="list"),
        pytest.param(("a", ("b", [b"c"])), "(<a>,(<b>,(X'63')))", id="nested"),
        pytest.param((), "()", id="empty"),
    ],
)
def test_no_backslash_escapes_renders_nested_sequences_recursively(value, text):
    # when
    result = prepare((value,), no_backslash_escapes=True)
    # then
    assert literal_texts(result) == [text]


def test_no_backslash_escapes_leaves_scalars_and_prepares_dict_values():
    # when
    result = prepare({"a": 1, "b": "s", "c": b"x"}, no_backslash_escapes=True)
    # then
    assert kinds(result) == ["int", "str", "SqlLiteral"]
    assert literal_texts(result) == [1, "s", "X'78'"]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        pytest.param("a'b", 'L("a\'b")', id="str"),
        pytest.param(5, "L(5)", id="int"),
        pytest.param(2.5, "L(2.5)", id="float"),
        pytest.param(None, "L(None)", id="none"),
        pytest.param(Decimal("1.5"), "L(Decimal('1.5'))", id="decimal"),
        pytest.param(date(2024, 2, 29), "L(datetime.date(2024, 2, 29))", id="date"),
        pytest.param(UUID(int=1), f"L({UUID(int=1)!r})", id="uuid"),
    ],
)
def test_render_leaves_known_types_to_the_connections_own_literal(value, text):
    # when
    result = rendered(value)
    # then
    assert result == text


def test_render_quotes_the_text_of_an_unknown_type_through_the_connections_escaping():
    # when
    result = rendered(Weird())
    # then
    assert result == f"'E<{WEIRD_TEXT}>'"


def test_render_renders_binary_values_as_hex_and_sequences_recursively():
    # when
    result = rendered((b"a", [Weird(), 1]))
    # then
    assert result == f"(X'61',('E<{WEIRD_TEXT}>',L(1)))"


def test_number_subclasses_become_their_text_like_mysqlclient_in_the_default_mode():
    # when
    result = prepare(number_subclass_params())
    # then
    assert result == (QUOTED_TEXT,) * 4
    assert kinds(result) == ["str"] * 4


def test_number_subclasses_are_rendered_as_quoted_text_under_no_backslash_escapes():
    # when
    texts = literal_texts(prepare(number_subclass_params(), no_backslash_escapes=True))
    # then
    assert texts == [f"'E<{QUOTED_TEXT}>'"] * 4


def test_render_gives_the_quoted_text_for_number_subclasses():
    # when
    texts = rendered_numbers()
    # then
    assert texts == [f"'E<{QUOTED_TEXT}>'"] * 4


def test_a_str_subclass_nested_in_a_sequence_is_sent_as_its_value_like_mysqlclient():
    # when
    result = prepare(nested_text_choice_params())
    # then
    assert result == (("va'l",), ("va'l",))
    assert kinds(result[0] + result[1]) == ["str", "str"]


def test_a_non_string_set_is_a_type_error_not_a_programming_error():
    # when / then: `_format` maps placeholder TypeErrors, not this one
    with pytest.raises(TypeError, match="set elements must be strings"):
        format_with({1}, "SELECT %s")


@pytest.mark.parametrize("no_backslash_escapes", [False, True], ids=["default", "no-backslash"])
@pytest.mark.parametrize("value", [float("-inf"), float("inf"), float("nan")], ids=repr)
@pytest.mark.parametrize("wrap", [wrap_top, wrap_in_tuple, wrap_in_list, wrap_in_dict])
def test_a_non_finite_float_is_a_programming_error_at_any_depth_in_both_modes(
    no_backslash_escapes, value, wrap
):
    # when / then
    with pytest.raises(ProgrammingError, match="can not be used with MySQL"):
        prepare(wrap(value), no_backslash_escapes=no_backslash_escapes)


@pytest.mark.parametrize(
    ("no_backslash_escapes", "expected"),
    [(False, "-inf"), (True, "'E<-inf>'")],
    ids=["default", "no-backslash"],
)
def test_a_float_subclass_with_a_non_finite_value_is_its_quoted_text(
    no_backslash_escapes, expected
):
    # when
    result = prepare((NonFiniteFloat("-inf"),), no_backslash_escapes=no_backslash_escapes)
    # then
    assert literal_texts(result) == [expected]


@pytest.mark.parametrize("no_backslash_escapes", [False, True], ids=["default", "no-backslash"])
@pytest.mark.parametrize("value", [Point(1, 2), TupleSubclass((1, 2)), FrozenSubclass({"a"})])
def test_a_container_subclass_is_its_quoted_str_in_both_modes(no_backslash_escapes, value):
    # when
    result = prepare((value,), no_backslash_escapes=no_backslash_escapes)
    # then
    assert literal_texts(result) == expected_subclass_text(value, no_backslash_escapes)


@pytest.mark.parametrize("no_backslash_escapes", [False, True], ids=["default", "no-backslash"])
@pytest.mark.parametrize("wrap", [wrap_top, wrap_in_tuple, wrap_in_list, wrap_in_dict, wrap_in_set])
@pytest.mark.parametrize("text", LONE_SURROGATES)
def test_a_lone_surrogate_is_a_unicode_encode_error_at_any_depth_in_both_modes(
    no_backslash_escapes, wrap, text
):
    # when / then
    with pytest.raises(UnicodeEncodeError):
        prepare(wrap(text), no_backslash_escapes=no_backslash_escapes)


@pytest.mark.parametrize("no_backslash_escapes", [False, True], ids=["default", "no-backslash"])
@pytest.mark.parametrize(
    "value",
    [SurrogateText(LONE_SURROGATES[0]), SurrogateInt(5), SurrogateObject()],
    ids=["str-subclass", "number-subclass-text", "unknown-type"],
)
def test_a_subclass_with_a_lone_surrogate_text_is_rejected(no_backslash_escapes, value):
    # when / then
    with pytest.raises(UnicodeEncodeError):
        prepare((value,), no_backslash_escapes=no_backslash_escapes)


def test_non_ascii_text_that_the_charset_can_encode_is_left_alone():
    # given
    params = ("h\u00e9llo \u20ac",)
    # when
    result = prepare(params)
    # then
    assert result == params


def test_a_lone_surrogate_in_an_unused_dict_key_is_rejected_like_mysqlclient():
    # when / then
    with pytest.raises(UnicodeEncodeError):
        prepare({LONE_SURROGATES[0]: 1, "a": 2})
