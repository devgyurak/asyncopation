"""MySQLConnection's transaction state machine (autocommit switch after a server-ended
transaction, stale status flags) against a fake raw connection."""

import pytest
from django.db import OperationalError

from tests.support.mysql_fakes import (
    end_transaction_with_failure,
    reset_with_failing_restore,
    restore_with_a_failing_first_attempt,
    resume_after,
)

BEGIN = "begin"


async def test_resume_does_nothing_outside_a_transaction():
    # when
    outcome = await resume_after(["resume"])
    # then
    assert outcome.calls == []


async def test_resume_does_nothing_while_the_server_still_has_the_transaction():
    # when
    outcome = await resume_after([BEGIN, "resume"])
    # then
    assert outcome.calls == ["begin"]
    assert outcome.autocommit_off is False


async def test_resume_switches_autocommit_off_when_the_server_ended_the_transaction():
    # when
    outcome = await resume_after([BEGIN, "server-ends", "resume"])
    # then
    assert outcome.calls == ["begin", "autocommit(False)"]
    assert outcome.autocommit_off is True


async def test_resume_does_not_switch_again_once_autocommit_is_off():
    # when
    outcome = await resume_after(
        [BEGIN, "server-ends", "resume", "server-ends", "resume", "resume"]
    )
    # then
    assert outcome.calls.count("autocommit(False)") == 1


async def test_resume_never_issues_a_second_begin():
    # when
    outcome = await resume_after([BEGIN, "server-ends", "resume"])
    # then
    assert outcome.calls.count("begin") == 1


@pytest.mark.parametrize("ending", ["commit", "rollback", "reset"])
async def test_ending_the_transaction_restores_autocommit(ending):
    # when
    outcome = await resume_after([BEGIN, "server-ends", "resume", ending])
    # then
    assert outcome.calls[-1] == "autocommit(True)"
    assert outcome.autocommit_off is False


@pytest.mark.parametrize("ending", ["commit", "rollback", "reset"])
async def test_ending_a_transaction_that_was_never_ended_by_the_server_does_not_touch_autocommit(
    ending,
):
    # when
    outcome = await resume_after([BEGIN, ending])
    # then
    assert "autocommit(True)" not in outcome.calls


async def test_a_new_transaction_after_the_restore_can_be_resumed_again():
    # when
    outcome = await resume_after(
        [BEGIN, "server-ends", "resume", "commit", BEGIN, "server-ends", "resume"]
    )
    # then
    assert outcome.calls.count("autocommit(False)") == 2
    assert outcome.calls.count("autocommit(True)") == 1


async def test_a_server_error_inside_a_transaction_marks_the_status_flags_stale():
    # when
    outcome = await resume_after([BEGIN, "fail"])
    # then
    assert outcome.status_stale is True


async def test_a_server_error_outside_a_transaction_does_not_mark_them_stale():
    # when
    outcome = await resume_after(["fail"])
    # then
    assert outcome.status_stale is False


async def test_resume_refreshes_stale_flags_with_one_cheap_statement_then_clears_the_mark():
    # when
    outcome = await resume_after([BEGIN, "fail", "resume", "resume"])
    # then
    assert outcome.calls.count("query(DO 0)") == 1
    assert outcome.status_stale is False


async def test_do_0_is_not_run_without_an_error_reply():
    # when
    outcome = await resume_after([BEGIN, "resume", "commit", BEGIN, "resume"])
    # then
    assert "query(DO 0)" not in outcome.calls


@pytest.mark.parametrize("step", [BEGIN, "commit", "rollback", "reset"])
async def test_begin_commit_rollback_and_reset_clear_the_stale_mark(step):
    # when
    outcome = await resume_after([BEGIN, "fail", step])
    # then
    assert outcome.status_stale is False
    assert "query(DO 0)" not in outcome.calls


async def test_a_failing_autocommit_restore_discards_the_connection():
    # when
    raised, connected, calls = await restore_with_a_failing_first_attempt()
    # then
    assert type(raised) is OperationalError  # the server's 1290, mapped
    assert raised.args[0] == 1290
    assert connected is False
    assert calls.count("autocommit(True) failed") == 1


@pytest.mark.parametrize("ending", ["commit", "rollback"])
async def test_resume_after_the_transaction_ended_does_nothing(ending):
    # when
    outcome = await resume_after([BEGIN, "server-ends", ending, "resume"])
    # then
    assert "autocommit(False)" not in outcome.calls
    assert "query(DO 0)" not in outcome.calls


@pytest.mark.parametrize(
    ("ending", "operation"),
    [
        ("commit", "commit"),
        ("rollback", "rollback"),
        ("commit", "autocommit"),
        ("rollback", "autocommit"),
    ],
)
async def test_a_failed_commit_rollback_or_autocommit_restore_discards_the_connection(
    ending, operation
):
    # when: a server error that does not drop the link (1317)
    raised, connected = await end_transaction_with_failure(ending, operation)
    # then: the session may still be inside a transaction, so it is never reused
    assert isinstance(raised, OperationalError)
    assert connected is False


async def test_a_failing_restore_during_reset_discards_the_connection():
    # when
    connected, calls = await reset_with_failing_restore()
    # then
    assert connected is False
    assert "closed" in calls


async def test_rollback_on_a_discarded_connection_makes_no_raw_calls():
    # when
    outcome = await resume_after([BEGIN, "discard", "rollback"])
    # then
    assert outcome.calls == ["begin", "closed"]


@pytest.mark.parametrize("entry", ["fetch_all", "execute", "execute_many", "stream"])
async def test_a_client_side_error_after_a_server_ended_transaction_does_no_round_trip(entry):
    # when
    outcome = await resume_after([BEGIN, "server-ends", f"bad-set:{entry}"])
    # then: formatting happens before the transaction is resumed
    assert outcome.calls == ["begin"]
    assert outcome.status_stale is False


async def test_a_wrong_placeholder_count_in_execute_many_does_no_round_trip_either():
    # when
    outcome = await resume_after([BEGIN, "server-ends", "bad-count"])
    # then: the whole batch is formatted before the transaction is resumed
    assert outcome.calls == ["begin"]
    assert outcome.status_stale is False


async def test_an_empty_batch_after_a_server_ended_transaction_does_no_round_trip():
    # when
    outcome = await resume_after([BEGIN, "server-ends", "empty-batch"])
    # then
    assert outcome.calls == ["begin"]
