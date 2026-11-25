"""`execute_many` is owned by the driver: how it rewrites and chunks, and what it rejects."""

import pytest
from django.db import ProgrammingError

from tests.support.mysql_batch import (
    INSERT,
    LIMIT,
    LONE_SURROGATES,
    chunk_count_with_last_statement_longer_by,
    plan,
    plan_generator,
    planning_seconds_for_a_long_run_of_blanks,
    statement_lengths_with_one_oversized_row,
)


def test_an_insert_with_values_becomes_one_multi_row_statement():
    # when
    statements = plan(INSERT, [(1,), (2,), (3,)])
    # then
    assert statements == ["INSERT INTO t (a) VALUES (1),(2),(3)"]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        pytest.param(
            "replace into t (a) values (%s)", "replace into t (a) values (1),(2)", id="replace"
        ),
        pytest.param("INSERT INTO t (a) VALUE (%s)", "INSERT INTO t (a) VALUE (1),(2)", id="value"),
        pytest.param(
            "INSERT INTO t (a) VALUES(%s);",
            "INSERT INTO t (a) VALUES(1),(2)",
            id="no-space-semicolon",
        ),
        pytest.param(
            "  INSERT INTO t (a)\n VALUES ( %s )  ",
            "INSERT INTO t (a)\n VALUES ( 1 ),( 2 )",
            id="whitespace",
        ),
        pytest.param(
            "INSERT INTO t (a) VALUES (%s) ON DUPLICATE KEY UPDATE a = VALUES(a)",
            "INSERT INTO t (a) VALUES (1),(2) ON DUPLICATE KEY UPDATE a = VALUES(a)",
            id="on-duplicate",
        ),
    ],
)
def test_the_documented_insert_shapes_are_rewritten(sql, expected):
    # when
    statements = plan(sql, [(1,), (2,)], strip=True)
    # then
    assert statements == [expected]


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(
            "INSERT INTO t (a) VALUES (%s) AS new ON DUPLICATE KEY UPDATE a = new.a", id="as-alias"
        ),
        pytest.param("INSERT INTO t (a) SELECT %s", id="insert-select"),
        pytest.param("INSERT INTO t (a) VALUES (%s, 1)", id="literal-in-values"),
        pytest.param("UPDATE t SET a = %s", id="update"),
        pytest.param("DELETE FROM t WHERE a = %s", id="delete"),
    ],
)
def test_every_other_statement_runs_once_per_parameter_set(sql):
    # when
    statements = plan(sql, [(1,), (2,), (3,)])
    # then
    assert len(statements) == 3


def test_a_percent_in_the_postfix_is_sent_as_it_is():
    # when
    statements = plan(
        "INSERT INTO t (a) VALUES (%s) ON DUPLICATE KEY UPDATE a = '%%'", [(1,), (2,)]
    )
    # then
    assert statements == ["INSERT INTO t (a) VALUES (1),(2) ON DUPLICATE KEY UPDATE a = '%%'"]


def test_a_doubled_percent_in_the_prefix_is_one_percent():
    # when
    statements = plan("INSERT INTO t (a) /* 5%% */ VALUES (%s)", [(1,)])
    # then
    assert statements == ["INSERT INTO t (a) /* 5% */ VALUES (1)"]


def test_a_batch_with_a_none_parameter_set_runs_per_row():
    # when
    statements = plan(INSERT, [(1,), None])
    # then
    assert statements == ["INSERT INTO t (a) VALUES (1)", INSERT]


def test_an_empty_batch_plans_no_statement():
    # when
    statements = plan(INSERT, [])
    # then
    assert statements == []


def test_dict_parameters_are_formatted_into_each_values_group():
    # when
    statements = plan(
        "INSERT INTO t (a, b) VALUES (%(a)s, %(b)s)", [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    )
    # then
    assert statements == ["INSERT INTO t (a, b) VALUES (1, 2),(3, 4)"]


def test_a_statement_exactly_at_the_size_limit_is_not_split():
    # when
    count = chunk_count_with_last_statement_longer_by(0)
    # then
    assert count == 1


def test_a_statement_one_byte_past_the_size_limit_is_split():
    # when
    count = chunk_count_with_last_statement_longer_by(1)
    # then
    assert count == 2


def test_an_oversized_row_travels_alone():
    # when
    lengths = statement_lengths_with_one_oversized_row()
    # then
    assert len(lengths) == 3
    assert lengths[1] > LIMIT


@pytest.mark.parametrize("bad", LONE_SURROGATES)
@pytest.mark.parametrize("sql", [INSERT, "UPDATE t SET a = %s"], ids=["rewritten", "per-row"])
def test_a_lone_surrogate_in_any_row_is_rejected_before_anything_is_planned(sql, bad):
    # when / then
    with pytest.raises(UnicodeEncodeError):
        plan(sql, [(1,), (2,), (bad,)])


@pytest.mark.parametrize("sql", [INSERT, "UPDATE t SET a = %s"], ids=["rewritten", "per-row"])
def test_a_bad_third_set_rejects_the_whole_batch(sql):
    # when / then
    with pytest.raises(TypeError, match="set elements must be strings"):
        plan(sql, [(1,), (2,), ({1},)])


@pytest.mark.parametrize("sql", [INSERT, "UPDATE t SET a = %s"], ids=["rewritten", "per-row"])
def test_a_wrong_parameter_count_in_a_later_set_is_a_programming_error(sql):
    # when / then
    with pytest.raises(ProgrammingError):
        plan(sql, [(1,), (2,), (3, 4)])


def test_a_long_run_of_blanks_after_the_values_group_is_planned_in_linear_time():
    # when
    seconds = planning_seconds_for_a_long_run_of_blanks(40_000)
    # then: a backtracking regex needed seconds here
    assert seconds < 0.5


def test_two_spaces_in_on_duplicate_run_per_row_like_mysqlclient():
    # when
    statements = plan("INSERT INTO t (a) VALUES (%s) ON  DUPLICATE KEY UPDATE a = 1", [(1,), (2,)])
    # then
    assert len(statements) == 2


@pytest.mark.parametrize("rows", [["ab", "cd"], [1, 2]], ids=["strings", "numbers"])
def test_a_batch_of_bare_scalars_is_a_programming_error(rows):
    # when / then
    with pytest.raises(ProgrammingError, match="tuples, lists or dicts"):
        plan(INSERT, rows)


def test_a_generator_batch_is_planned_like_a_list():
    # when
    statements = plan_generator([(1,), (2,)])
    # then
    assert statements == ["INSERT INTO t (a) VALUES (1),(2)"]


@pytest.mark.parametrize("sql", [INSERT, "UPDATE t SET a = %s"], ids=["rewritten", "per-row"])
def test_a_nan_in_a_later_set_rejects_the_whole_batch(sql):
    # when / then
    with pytest.raises(ProgrammingError, match="can not be used with MySQL"):
        plan(sql, [(1.0,), (float("nan"),)])


def test_an_unused_non_ascii_dict_key_in_a_later_set_rejects_the_whole_batch():
    # when / then
    with pytest.raises(UnicodeEncodeError):
        plan("INSERT INTO t (a) VALUES (%(a)s)", [{"a": 1}, {"a": 2, LONE_SURROGATES[0]: 3}])
