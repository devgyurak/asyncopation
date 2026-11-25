"""`execute_many` (the driver rewrites and chunks the batch itself) equals Django's sync
`executemany`: rows, rowcount, the number of statements the server ran, and the error class."""

import pytest

from tests.support.mysql_batch_diff import (
    batch_vs_sync,
    named_rows,
    one_shot_generator_of_two_rows,
    rows_ending_with_a_duplicate,
)
from tests.support.mysql_env import MARIADB_ONLY, MYSQL_FAMILY, MYSQL_ONLY
from tests.support.mysql_scenarios import cancelled_between_statements

pytestmark = MYSQL_FAMILY

INSERT = "INSERT INTO {t} (name, qty) VALUES (%s, %s)"
CASES = [
    pytest.param(INSERT, [("x", 1), ("y", 2), ("z", 3)], id="insert"),
    pytest.param("insert into {t} (name, qty) values (%s, %s)", [("x", 1), ("y", 2)], id="lower"),
    pytest.param("INSERT INTO {t} (name, qty) VALUE (%s, %s)", [("x", 1), ("y", 2)], id="value"),
    pytest.param(
        "INSERT INTO {t} (name, qty) VALUES(%s,%s);", [("x", 1), ("y", 2)], id="semicolon"
    ),
    pytest.param(
        "INSERT INTO {t} (name, qty)\n  VALUES ( %s , %s )  ", [("x", 1), ("y", 2)], id="whitespace"
    ),
    pytest.param(
        "INSERT INTO {t} (name, qty) VALUES (%(n)s, %(q)s)",
        [{"n": "x", "q": 1}, {"n": "y", "q": 2}],
        id="dict",
    ),
    pytest.param(
        "REPLACE INTO {t} (name, qty) VALUES (%s, %s)", [("a", 5), ("y", 2)], id="replace"
    ),
    pytest.param(
        INSERT + " ON DUPLICATE KEY UPDATE qty = qty + 1", [("a", 1), ("y", 2)], id="on-duplicate"
    ),
    pytest.param(
        INSERT + " ON DUPLICATE KEY UPDATE name = CONCAT(name, '%%')",
        [("a", 1), ("y", 2)],
        id="on-duplicate-percent",
    ),
    pytest.param(
        INSERT + " ON DUPLICATE KEY UPDATE name = CONCAT(name, %s)",
        [("a", 1, "!"), ("y", 2, "!")],
        id="on-duplicate-placeholder",
    ),
    pytest.param("INSERT INTO {t} (name, qty) SELECT %s, %s", [("x", 1), ("y", 2)], id="select"),
    pytest.param("UPDATE {t} SET qty = %s WHERE name = %s", [(4, "a"), (5, "a")], id="update"),
    pytest.param("DELETE FROM {t} WHERE name = %s", [("a",), ("zzz",)], id="delete"),
    pytest.param("INSERT INTO {t} /* 5%% */ (name, qty) VALUES (%s, %s)", [("x", 1)], id="prefix"),
    pytest.param(
        "INSERT INTO {t} /* 5% */ (name, qty) VALUES (%s, %s)", [("x", 1)], id="prefix-bare"
    ),
    pytest.param(
        "UPDATE {t} SET qty = qty + 1 WHERE name = 'a'", [None, None], id="update-none-sets"
    ),
    pytest.param(INSERT, [("x", 1), ("a", 2), ("y", 3)], id="duplicate-in-the-middle"),
    pytest.param(INSERT, [("x", 1), ("y",), ("z", 3)], id="too-few-parameters"),
]


@pytest.mark.parametrize(("template", "batch"), CASES)
async def test_a_batch_equals_djangos_sync_executemany(scratch, readonly_db, template, batch):
    # when
    result = await batch_vs_sync(scratch, template, batch)
    # then
    assert result.async_outcome == result.sync_outcome


@pytest.mark.parametrize("width", [4, 5, 6, 7, 8])
async def test_a_batch_past_the_statement_size_limit_is_split_where_sync_splits_it(
    scratch, readonly_db, width
):
    # the row width moves the chunk boundary; a duplicate at the end fails the last statement, so
    # the rows left behind show where the split fell
    # given
    batch = rows_ending_with_a_duplicate(width)
    # when
    result = await batch_vs_sync(scratch, INSERT, batch)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.statements > 1


async def test_an_empty_batch_runs_nothing_and_reports_no_rows(scratch, readonly_db):
    # when: Django's sync `rowcount` is whatever the previous statement left, so only ours is pinned
    result = await batch_vs_sync(scratch, INSERT, [])
    # then
    assert (result.async_outcome.result, result.async_outcome.statements) == (0, 0)


async def test_a_server_error_in_a_later_chunk_leaves_the_same_rows_as_sync(scratch, readonly_db):
    # given: the 7000 rows span two statements and the duplicate is in the second
    batch = [*named_rows(7000), ("n00000000", 1)]
    # when
    result = await batch_vs_sync(scratch, INSERT, batch)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.statements == 2


async def test_a_client_error_in_a_later_set_applies_nothing_where_sync_applies_the_earlier_rows(
    scratch, readonly_db
):
    # when: a documented difference (ADR-0003 Decision 5): the driver validates the whole batch first
    result = await batch_vs_sync(
        scratch, "UPDATE {t} SET qty = %s WHERE name = %s", [(4, "a"), (5, "a"), (6,)]
    )
    # then: the same error, but sync already ran the first two statements
    assert result.async_outcome.result == result.sync_outcome.result
    assert result.async_outcome.rows == [("a", 0)]
    assert result.sync_outcome.rows == [("a", 5)]


async def test_a_batch_cancelled_between_statements_discards_the_connection(scratch, readonly_db):
    # when
    timed_out, replaced, applied = await cancelled_between_statements(scratch)
    # then: the statements after the cancel were never sent
    assert timed_out is True
    assert replaced is True
    assert 3 <= applied <= 4


@MYSQL_ONLY
@pytest.mark.parametrize(
    ("template", "batch"),
    [
        pytest.param(
            INSERT + " AS new ON DUPLICATE KEY UPDATE qty = new.qty",
            [("a", 7), ("y", 2)],
            id="as-alias",
        ),
        pytest.param(
            INSERT + " AS new ON DUPLICATE KEY UPDATE qty = new.qty + LENGTH('%%')",
            [("a", 7), ("y", 2)],
            id="as-alias-percent",
        ),
    ],
)
async def test_an_insert_with_an_alias_runs_per_row_like_sync_on_mysql(
    scratch, readonly_db, template, batch
):
    # when
    result = await batch_vs_sync(scratch, template, batch)
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.statements == len(batch)


@MARIADB_ONLY
async def test_an_insert_with_an_alias_fails_like_sync_on_mariadb(scratch, readonly_db):
    # when: MariaDB has no `AS alias` in INSERT
    result = await batch_vs_sync(
        scratch, INSERT + " AS new ON DUPLICATE KEY UPDATE qty = new.qty", [("a", 7), ("y", 2)]
    )
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.statements == 0


@MARIADB_ONLY
async def test_an_insert_returning_runs_per_row_like_sync_on_mariadb(scratch, readonly_db):
    # when
    result = await batch_vs_sync(scratch, INSERT + " RETURNING name", [("x", 1), ("y", 2)])
    # then
    assert result.async_outcome == result.sync_outcome
    assert result.async_outcome.statements == 2


async def test_a_generator_batch_runs_like_a_list(scratch, readonly_db):
    # when
    result = await batch_vs_sync(scratch, INSERT, one_shot_generator_of_two_rows)
    # then
    assert result.async_outcome == result.sync_outcome
    assert (result.async_outcome.result, len(result.async_outcome.rows)) == (2, 3)


async def test_a_client_error_in_a_later_chunk_applies_nothing_where_sync_applies_the_first_chunk(
    scratch, readonly_db
):
    # when: the 7000 rows span two statements; the bad row is after both
    result = await batch_vs_sync(scratch, INSERT, [*named_rows(7000), ("x",)])
    # then: the same error, but nothing was sent before the whole batch was checked
    assert result.async_outcome.result == result.sync_outcome.result
    assert result.async_outcome.rows == [("a", 0)]
    assert len(result.sync_outcome.rows) > 1


async def test_a_none_set_in_an_insert_batch_runs_per_row_where_sync_builds_one_failing_statement(
    scratch, readonly_db
):
    # when: a documented difference (ADR-0003 Decision 5); Django never passes None
    result = await batch_vs_sync(scratch, INSERT, [("x", 1), None])
    # then
    assert result.async_outcome.result == result.sync_outcome.result  # the same syntax error
    assert (result.async_outcome.statements, result.sync_outcome.statements) == (1, 0)
    assert result.async_outcome.rows == [("a", 0), ("x", 1)]
    assert result.sync_outcome.rows == [("a", 0)]


async def test_a_literal_between_named_placeholders_runs_per_row_where_sync_rewrites(
    scratch, readonly_db
):
    # when: a documented difference: the driver rewrites only placeholder-only VALUES groups
    template = "INSERT INTO {t} (name, id, qty) VALUES (%(n)s, NULL, %(q)s)"
    result = await batch_vs_sync(scratch, template, [{"n": "x", "q": 1}, {"n": "y", "q": 2}])
    # then: the same rows, one statement per row against one
    assert result.async_outcome.rows == result.sync_outcome.rows
    assert (result.async_outcome.statements, result.sync_outcome.statements) == (2, 1)


@pytest.mark.parametrize(("rows", "sync_statements"), [(1, 1), (2, 0)])
async def test_an_on_duplicate_tail_ending_in_a_named_placeholder_is_sent_verbatim(
    scratch, readonly_db, rows, sync_statements
):
    # when: a documented difference: mysqlclient formats a tail that ends in `)` per row
    template = (
        "INSERT INTO {t} (name, qty) VALUES (%(n)s, %(q)s) "
        "ON DUPLICATE KEY UPDATE name = CONCAT(name, %(c)s)"
    )
    batch = [{"n": "a", "q": 1, "c": "!"}, {"n": "y", "q": 2, "c": "!"}][:rows]
    result = await batch_vs_sync(scratch, template, batch)
    # then: the driver's one rewritten statement keeps the placeholder and the server rejects it
    assert result.async_outcome.result == "django.db.utils.ProgrammingError(1064)"
    assert result.async_outcome.rows == [("a", 0)]
    assert result.sync_outcome.statements == sync_statements
