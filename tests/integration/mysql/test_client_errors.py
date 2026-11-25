"""Client-side formatting errors leave the connection and its transaction intact."""

import pytest
from django.db import ProgrammingError

from tests.support.mysql_batch import LONE_SURROGATES
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import (
    bad_batch,
    bad_execute_many_inside_a_transaction,
    bad_parameter_count_inside_a_transaction,
    client_error_inside_a_transaction,
    many_with_a_percent_in_the_insert_postfix,
    nan_parameter,
)

pytestmark = MYSQL_FAMILY

EXPECTED_ROWS = {False: [], True: ["a"]}
OPERATIONS = pytest.mark.parametrize("operation", ["fetch_all", "execute", "stream"])


@OPERATIONS
async def test_a_bad_parameter_count_raises_programming_error_from_the_client_side_formatting(
    single_driver, scratch, operation
):
    # when
    outcome = await bad_parameter_count_inside_a_transaction(single_driver, scratch, operation)
    # then
    assert type(outcome.error) is ProgrammingError


@OPERATIONS
async def test_a_bad_parameter_count_keeps_the_connection_and_its_transaction(
    single_driver, scratch, operation
):
    # when
    outcome = await bad_parameter_count_inside_a_transaction(single_driver, scratch, operation)
    # then
    assert outcome.same_connection is True
    assert outcome.next_rows == [(1,)]
    assert outcome.committed == ["a"]


BATCHES = pytest.mark.parametrize("kind", ["update", "insert"])


@BATCHES
async def test_execute_many_with_a_bad_parameter_count_raises_programming_error(
    single_driver, scratch, kind
):
    # when
    outcome = await bad_execute_many_inside_a_transaction(single_driver, scratch, kind)
    # then
    assert type(outcome.error) is ProgrammingError


@BATCHES
async def test_execute_many_with_a_bad_parameter_count_keeps_the_transaction(
    single_driver, scratch, kind
):
    # when
    outcome = await bad_execute_many_inside_a_transaction(single_driver, scratch, kind)
    # then
    assert outcome.same_connection is True
    assert outcome.next_rows == [(1,)]
    assert outcome.committed == ["a"]


@pytest.mark.parametrize("transactional", [False, True], ids=["autocommit", "transaction"])
@OPERATIONS
async def test_a_nan_parameter_raises_programming_error_and_keeps_the_connection(
    single_driver, scratch, operation, transactional
):
    # when
    outcome = await nan_parameter(single_driver, scratch, operation, transactional=transactional)
    # then
    assert type(outcome.error) is ProgrammingError
    assert outcome.same_connection is True
    assert outcome.next_rows == [(1,)]
    assert outcome.committed == EXPECTED_ROWS[transactional]


ALL_CALLS = pytest.mark.parametrize("operation", ["fetch_all", "execute", "execute_many", "stream"])
SQL_MODES = pytest.mark.parametrize(
    "options",
    [{}, {"init_command": "SET sql_mode='NO_BACKSLASH_ESCAPES'"}],
    ids=["default-sql-mode", "no-backslash-escapes"],
)
REJECTED = pytest.mark.parametrize(
    ("sql", "params", "error_class"),
    [
        pytest.param("SELECT %s", ({1},), TypeError, id="set-of-int"),
        pytest.param("SELECT %s", (float("nan"),), ProgrammingError, id="nan"),
        pytest.param("SELECT %s", (float("-inf"),), ProgrammingError, id="neg-inf"),
        pytest.param("SELECT %s", (float("inf"),), ProgrammingError, id="inf"),
        pytest.param("SELECT 1 IN %s", ((float("nan"),),), ProgrammingError, id="nested-nan"),
        pytest.param("SELECT 1 IN %s", ([float("-inf")],), ProgrammingError, id="nested-neg-inf"),
        pytest.param("SELECT %(a)s", {"b": 1}, KeyError, id="missing-key"),
        pytest.param("SELECT %s, %s", (1,), ProgrammingError, id="too-few-parameters"),
        pytest.param("SELECT %s", (LONE_SURROGATES[0],), UnicodeEncodeError, id="surrogate-dc80"),
        pytest.param("SELECT %s", (LONE_SURROGATES[2],), UnicodeEncodeError, id="surrogate-d800"),
        pytest.param(
            "SELECT 1 /* " + LONE_SURROGATES[0] + " */",
            None,
            UnicodeEncodeError,
            id="surrogate-in-sql",
        ),
    ],
)


@ALL_CALLS
@SQL_MODES
@REJECTED
async def test_a_value_the_client_rejects_keeps_the_connection_and_its_transaction(
    scratch, operation, options, sql, params, error_class
):
    # when
    outcome = await client_error_inside_a_transaction(scratch, operation, sql, params, options)
    # then
    assert type(outcome.error) is error_class
    assert outcome.same_connection is True
    assert outcome.next_rows == [(1,)]
    assert outcome.committed == ["a"]


async def test_execute_many_accepts_a_literal_percent_in_the_postfix_of_an_insert_it_rewrites(
    single_driver, scratch
):
    # when
    rowcount, names = await many_with_a_percent_in_the_insert_postfix(single_driver, scratch)
    # then
    assert rowcount == 2
    assert sorted(names) == ["a", "b"]


NAN = float("nan")
UPDATE = "UPDATE {t} SET qty = %(q)s WHERE name = %(n)s"
INSERT_ROW = "INSERT INTO {t} (name, qty) VALUES (%(n)s, %(q)s)"
GOOD = {"q": 9, "n": "a"}
REJECTED_BATCHES = pytest.mark.parametrize(
    ("template", "batch", "error_class"),
    [
        pytest.param(UPDATE, [GOOD, {"q": 9}], KeyError, id="update-missing-key"),
        pytest.param(UPDATE, [GOOD, {"q": NAN, "n": "a"}], ProgrammingError, id="update-nan"),
        pytest.param(
            UPDATE,
            [GOOD, GOOD, {"q": 9, "n": LONE_SURROGATES[1]}],
            UnicodeEncodeError,
            id="update-surrogate",
        ),
        pytest.param(
            INSERT_ROW,
            [{"n": "x", "q": 1}, {"n": "y", "q": 1}, {"b": 1}],
            KeyError,
            id="insert-missing-key",
        ),
        pytest.param(
            INSERT_ROW,
            [{"n": "x", "q": 1}, {"n": "y", "q": NAN}],
            ProgrammingError,
            id="insert-nan",
        ),
        pytest.param(
            INSERT_ROW,
            [{"n": "x", "q": 1}, {"n": LONE_SURROGATES[0], "q": 1}],
            UnicodeEncodeError,
            id="insert-surrogate",
        ),
    ],
)


@pytest.mark.parametrize("transactional", [False, True], ids=["autocommit", "transaction"])
@REJECTED_BATCHES
async def test_a_batch_with_a_rejected_later_set_applies_nothing_and_keeps_the_connection(
    scratch, template, batch, error_class, transactional
):
    # when
    outcome = await bad_batch(scratch, template, batch, transactional=transactional)
    # then: the first sets were never sent either
    assert type(outcome.error) is error_class
    assert outcome.same_connection is True
    assert outcome.next_rows == [("a", 0)]
    assert outcome.committed == ["a"]
