"""A transaction the server ends by itself must not turn later statements into autocommits."""

import pytest

from tests.support.mysql_env import MYSQL_FAMILY, admin_query
from tests.support.mysql_txn import (
    autocommit_after_ending,
    driver_ddl_in_transaction,
    driver_ddl_then_row_lock,
    driver_deadlock_victim,
    driver_lock_tables_in_transaction,
    stream_body_error_in_a_resumed_transaction,
    sync_ddl_in_transaction,
    sync_ddl_then_row_lock,
    sync_deadlock_victim,
    sync_lock_tables_in_transaction,
)

pytestmark = MYSQL_FAMILY

ENTRIES = pytest.mark.parametrize("entry", ["execute", "fetch_all", "execute_many", "stream"])


async def test_after_a_ddl_implicit_commit_a_rollback_undoes_the_later_statements(
    scratch, readonly_db
):
    # when
    outcome = await driver_ddl_in_transaction(scratch)
    # then
    assert outcome.survivors == ["a", "inside:a", "inside:b"]


async def test_the_ddl_script_leaves_the_same_rows_as_django(scratch, readonly_db):
    # when
    ours = (await driver_ddl_in_transaction(scratch)).survivors
    await admin_query(f"DELETE FROM {scratch}")
    reference = await sync_ddl_in_transaction(scratch)
    # then
    assert ours == reference.survivors


async def test_savepoints_and_the_connection_survive_a_ddl_implicit_commit(scratch, readonly_db):
    # when
    outcome = await driver_ddl_in_transaction(scratch)
    # then
    assert outcome.same_connection is True
    assert "inside:c" not in outcome.survivors


@ENTRIES
async def test_a_statement_after_a_ddl_implicit_commit_runs_in_a_new_transaction(
    scratch, readonly_db, entry
):
    # when
    outcome = await driver_ddl_then_row_lock(scratch, entry)
    # then: the row lock is still held, so another connection waits and times out (1205)
    assert outcome.lock_error == 1205
    assert outcome.flag_inside is True
    assert outcome.autocommit_after == 1
    assert outcome.autocommit_flag_off is False


async def test_django_sync_blocks_another_connection_after_a_ddl_implicit_commit(
    scratch, readonly_db
):
    # when
    reference = await sync_ddl_then_row_lock(scratch)
    # then
    assert reference.lock_error == 1205


@ENTRIES
async def test_after_a_deadlock_a_rollback_undoes_the_later_statements(scratch, readonly_db, entry):
    # when
    outcome = await driver_deadlock_victim(scratch, entry)
    # then
    assert outcome.error_code == 1213
    assert outcome.survivors == []


@ENTRIES
async def test_after_a_deadlock_the_next_statement_holds_its_locks_in_a_new_transaction(
    scratch, readonly_db, entry
):
    # when
    outcome = await driver_deadlock_victim(scratch, entry)
    # then
    assert outcome.lock_error == 1205


async def test_a_deadlock_leaves_the_same_connection_in_use(scratch, readonly_db):
    # when
    outcome = await driver_deadlock_victim(scratch)
    # then
    assert outcome.same_connection is True


async def test_django_sync_shows_the_same_deadlock_behaviour(scratch, readonly_db):
    # when
    reference = await sync_deadlock_victim(scratch)
    # then
    assert reference.error_code == 1213
    assert reference.survivors == []
    assert reference.lock_error == 1205


async def test_lock_tables_inside_a_transaction_keeps_its_lock_for_the_next_statement(
    scratch, readonly_db
):
    # when
    outcome = await driver_lock_tables_in_transaction(scratch)
    # then
    assert outcome.lock_error == 1205
    assert outcome.autocommit_after == 1


async def test_django_sync_keeps_lock_tables_locked_for_the_next_statement(scratch, readonly_db):
    # when
    reference = await sync_lock_tables_in_transaction(scratch)
    # then
    assert reference.lock_error == 1205


@pytest.mark.parametrize("how", ["commit", "rollback", "release"])
async def test_the_connection_is_back_in_autocommit_mode_once_the_transaction_ends(
    scratch, readonly_db, how
):
    # when
    outcome = await autocommit_after_ending(scratch, how)
    # then: the transaction was resumed with autocommit off, and is back on once it ended
    assert outcome.autocommit_before == 0
    assert outcome.autocommit_after == 1
    assert outcome.autocommit_flag_off is False
    assert outcome.same_connection is True


async def test_a_stream_body_error_in_a_resumed_transaction_keeps_the_connection(
    scratch, readonly_db
):
    # when
    outcome = await stream_body_error_in_a_resumed_transaction(scratch)
    # then
    assert type(outcome.body_error) is ValueError
    assert outcome.same_connection is True
    assert outcome.next_rows == [(1,)]


async def test_a_rollback_after_that_stream_error_undoes_the_later_statements(scratch, readonly_db):
    # when
    outcome = await stream_body_error_in_a_resumed_transaction(scratch)
    # then
    assert outcome.survivors == []


@pytest.mark.parametrize("entry", ["execute", "execute_many"])
async def test_a_deadlock_in_the_second_statement_of_a_batch_is_resumed_like_any_other(
    scratch, readonly_db, entry
):
    # when
    outcome = await driver_deadlock_victim(scratch, entry, victim="execute_many")
    # then: the batch failed with the deadlock, the next statement is in a new transaction that a
    # rollback undoes, and its row lock is held
    assert outcome.error_code == 1213
    assert outcome.survivors == []
    assert outcome.lock_error == 1205
