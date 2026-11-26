"""Drift: the ported MySQL wrapper attributes equal Django's source (read, never imported).

Unlike ``test_mysql_wrapper_drift.py`` this needs no mysqlclient, so it runs in every environment,
the Django-main canary included.
"""

import pytest

from tests.support.mysql_drift import EXPECTED_PORTED
from tests.support.mysql_source_drift import (
    APPLY_SERVER_INFO_SOURCE,
    COMPOSED_SOURCES,
    SAME_ASSIGNMENTS,
    SAME_FUNCTIONS,
    SERVER_DATA_SOURCE,
    VERSION_SOURCE,
    composed_classes_pair,
    composed_classes_pair_of,
    dict_shape_of,
    differing_assignments,
    differing_functions,
    django_server_data_decorators,
    django_server_data_decorators_of,
    injected_attribute_mismatches,
    injected_attribute_mismatches_of,
    injected_attributes_of,
    member_names_of,
    mysql_version_differs,
    row_index_coverage_pair,
    row_index_coverage_pair_of,
    row_indexes_of,
    row_order_pair,
    row_order_pair_of,
    server_data_bool_keys_pair,
    server_data_keys_pair,
    stale_accounted_members,
    unaccounted_members,
    version_pattern_pair,
    version_statements_of,
)


def test_every_django_wrapper_member_is_ported_composed_or_deliberately_left_out():
    # when
    unknown = unaccounted_members()
    # then
    assert unknown == set()


def test_nothing_is_listed_as_ported_composed_or_left_out_that_django_no_longer_defines():
    # when
    stale = stale_accounted_members()
    # then
    assert stale == set()


def test_the_ported_methods_have_the_ast_of_djangos():
    # when
    differing = differing_functions()
    # then
    assert differing == []


def test_the_ported_class_attributes_have_the_value_of_djangos():
    # when
    differing = differing_assignments()
    # then
    assert differing == []


def test_mysql_version_differs_from_django_only_in_the_message_of_its_error():
    # when
    differs = mysql_version_differs()
    # then
    assert differs is False


def test_the_version_pattern_is_the_one_in_djangos_source():
    # when
    ours, theirs = version_pattern_pair()
    # then
    assert ours == theirs


@pytest.mark.parametrize(
    "names", [SAME_FUNCTIONS, SAME_ASSIGNMENTS], ids=["functions", "attributes"]
)
def test_the_compared_names_are_all_ported(names):
    # when
    missing = set(names) - EXPECTED_PORTED
    # then
    assert missing == set()


def test_the_server_data_dict_has_the_keys_of_djangos_query_result():
    # when
    theirs, ours = server_data_keys_pair()
    # then
    assert ours == theirs


def test_the_server_data_flags_are_coerced_with_bool_like_in_djangos_query_result():
    # when
    theirs, ours = server_data_bool_keys_pair()
    # then
    assert ours == theirs


@pytest.mark.parametrize(
    ("variant", "other"),
    [
        (VERSION_SOURCE.format(message="'a'"), VERSION_SOURCE.format(message="f'b {self.info}'")),
    ],
    ids=["message"],
)
def test_the_version_comparison_ignores_the_message_of_the_error(variant, other):
    # when
    same = version_statements_of(variant) == version_statements_of(other)
    # then
    assert same is True


@pytest.mark.parametrize(
    "mutated",
    [
        VERSION_SOURCE.format(message="'a'").replace("if not match", "if match"),
        VERSION_SOURCE.format(message="'a'").replace("Exception(", "ValueError("),
        VERSION_SOURCE.format(message="'a'").replace("match.groups()", "match.groups()[:2]"),
        VERSION_SOURCE.format(message="'a'").replace("raise Exception('a')", "pass"),
    ],
    ids=["test", "exception-type", "return", "no-raise"],
)
def test_the_version_comparison_notices_a_changed_test_error_type_or_return(mutated):
    # when
    same = version_statements_of(mutated) == version_statements_of(
        VERSION_SOURCE.format(message="'a'")
    )
    # then
    assert same is False


def test_the_dict_shape_records_the_keys_and_which_values_are_wrapped_in_bool():
    # when
    shape = dict_shape_of("{'a': bool(x[0]), 'b': x[1], 'c': int(x[2]), 'd': bool(x, y)}")
    # then
    assert shape == {"a": True, "b": False, "c": False, "d": False}


def test_the_member_names_include_tuple_targets_nested_classes_and_annotated_names():
    # when
    names = member_names_of(
        "class C:\n    a, (b, c) = 1, (2, 3)\n    d: int = 4\n    e = f = 5\n"
        "    class Inner: ...\n    def m(self): ...\n"
    )
    # then
    assert names == {"a", "b", "c", "d", "e", "f", "Inner", "m"}


@pytest.mark.parametrize(
    "source",
    ["class C:\n    x.y = 1\n", "class C:\n    x[0] = 1\n", "class C:\n    *a, b = [1, 2]\n"],
    ids=["attribute", "subscript", "starred"],
)
def test_a_member_assigned_through_an_unsupported_target_fails_loudly(source):
    # when / then
    with pytest.raises(ValueError, match="unsupported assignment target"):
        member_names_of(source)


def test_the_composed_class_attributes_name_the_same_classes_as_django():
    # when
    ours, theirs = composed_classes_pair()
    # then
    assert ours == theirs


def test_the_composed_class_comparison_accepts_identical_sources():
    # when
    ours, theirs = composed_classes_pair_of(
        COMPOSED_SOURCES["django"].format(features="DatabaseFeatures"),
        COMPOSED_SOURCES["ours"],
        COMPOSED_SOURCES["bridge"],
    )
    # then
    assert ours == theirs


def test_the_composed_class_comparison_notices_a_class_that_django_swapped():
    # when
    ours, theirs = composed_classes_pair_of(
        COMPOSED_SOURCES["django"].format(features="OtherFeatures"),
        COMPOSED_SOURCES["ours"],
        COMPOSED_SOURCES["bridge"],
    )
    # then
    assert ours != theirs


def test_mysql_server_data_is_a_cached_property_in_djangos_source():
    # when
    decorators = django_server_data_decorators()
    # then
    assert decorators == ["cached_property"]


@pytest.mark.parametrize("decorator", ["property", "functools.cache"], ids=["property", "cache"])
def test_the_decorator_comparison_notices_a_data_descriptor(decorator):
    # when
    decorators = django_server_data_decorators_of(f"@{decorator}\ndef mysql_server_data(self): ...")
    # then
    assert decorators != ["cached_property"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("class C:\n    if x:\n        a = 1\n    else:\n        b = 2\n", {"a", "b"}),
        (
            "class C:\n    try:\n        a = 1\n    except E:\n        b = 2\n"
            "    finally:\n        c = 3\n",
            {"a", "b", "c"},
        ),
        (
            "class C:\n    with x:\n        def a(self): ...\n        b = 2\n    c = 3\n",
            {"a", "b", "c"},
        ),
        (
            "class C:\n    if x:\n        try:\n            a = 1\n        except E:\n"
            "            b = 2\n    c = 3\n",
            {"a", "b", "c"},
        ),
        ("class C:\n    for i in x:\n        a = 1\n    else:\n        b = 2\n", {"a", "b"}),
        ("class C:\n    while x:\n        a = 1\n    else:\n        b = 2\n", {"a", "b"}),
        (
            "class C:\n    match x:\n        case 1:\n            a = 1\n"
            "        case _:\n            b = 2\n",
            {"a", "b"},
        ),
        (
            "class C:\n    async def f(self): ...\n"
            "    for i in x:\n        if y:\n            a = 1\n        else:\n            b = 2\n",
            {"f", "a", "b"},
        ),
        (
            "class C:\n    try:\n        a = 1\n    except* E:\n        b = 2\n"
            "    else:\n        c = 3\n    finally:\n        d = 4\n",
            {"a", "b", "c", "d"},
        ),
    ],
    ids=[
        "if-else",
        "try-except-finally",
        "with",
        "nested-if-try",
        "for-else",
        "while-else",
        "match",
        "for-with-if-else",
        "try-star",
    ],
)
def test_the_member_names_include_members_declared_inside_class_level_statements(source, expected):
    # when
    names = member_names_of(source)
    # then
    assert names == expected


@pytest.mark.parametrize(
    "source",
    ["{**extra, 'a': 1}", "{'a': 1, **extra}", "{key: 1}", "{1: 2}"],
    ids=["leading-spread", "trailing-spread", "computed-key", "non-string-key"],
)
def test_a_dict_key_built_by_a_spread_or_computed_fails_loudly(source):
    # when / then
    with pytest.raises(ValueError, match="unsupported dict key"):
        dict_shape_of(source)


def test_the_server_data_row_order_is_the_field_order_of_the_server_info():
    # when
    by_row, by_field = row_order_pair()
    # then
    assert by_row == by_field


def test_the_server_data_row_indexes_are_exactly_the_positions_of_the_server_info_fields():
    # when
    indexes, positions = row_index_coverage_pair()
    # then
    assert indexes == positions


def test_the_row_index_check_notices_a_gap_in_the_row_indexes():
    # when
    indexes, positions = row_index_coverage_pair_of(SERVER_DATA_SOURCE.format(sql_mode=1, engine=3))
    # then
    assert (indexes, positions) == ([0, 1, 3, 3, 4, 5], [0, 1, 2, 3, 4, 5])


def test_the_server_data_row_order_check_accepts_the_expected_source():
    # when
    by_row, by_field = row_order_pair_of(SERVER_DATA_SOURCE.format(sql_mode=1, engine=2))
    # then
    assert by_row == by_field


def test_the_server_data_row_order_check_notices_two_swapped_row_indexes():
    # when
    by_row, by_field = row_order_pair_of(SERVER_DATA_SOURCE.format(sql_mode=2, engine=1))
    # then
    assert by_row != by_field


def test_the_server_data_row_indexes_read_a_bool_wrapped_subscript():
    # when
    indexes = row_indexes_of("{'a': row[0], 'b': bool(row[3])}")
    # then
    assert indexes == {"a": 0, "b": 3}


@pytest.mark.parametrize(
    "value",
    ["row[i]", "row[0:2]", "bool(row)", "other"],
    ids=["name", "slice", "bool", "name-only"],
)
def test_a_server_data_value_that_is_not_a_row_subscript_fails_loudly(value):
    # when / then
    with pytest.raises(ValueError, match="expected row"):
        row_indexes_of("{'a': " + value + "}")


def test_the_injected_server_data_reads_the_info_attribute_named_like_its_key():
    # when
    mismatches = injected_attribute_mismatches()
    # then
    assert mismatches == {}


def test_the_injected_attribute_check_reports_keys_that_read_another_attribute():
    # when
    mismatches = injected_attribute_mismatches_of(
        APPLY_SERVER_INFO_SOURCE.format(entries="{'a': info.b, 'c': bool(info.d), 'e': info.e}")
    )
    # then
    assert mismatches == {"a": "b", "c": "d"}


def test_the_injected_attributes_are_read_through_bool_or_directly():
    # when
    attributes = injected_attributes_of("{'a': info.x, 'b': bool(info.y)}")
    # then
    assert attributes == {"a": "x", "b": "y"}


def test_an_injected_value_that_is_not_an_info_attribute_fails_loudly():
    # when / then
    with pytest.raises(ValueError, match="expected info"):
        injected_attributes_of("{'a': 3}")
