"""What breaks a pinned transaction: a lost connection or a cancellation, never an ordinary error;
and the bookkeeping of `begin` / `commit` / `rollback` that decides whether a cancel counts."""

import pytest
from django.db import DatabaseError, IntegrityError

from tests.support.executors.failures import (
    broken_after,
    disconnect_in_statement,
    error_in_transaction,
)

P = pytest.param


@pytest.mark.parametrize(("begin", "broken"), [(True, True), (False, False)])
async def test_a_lost_connection_breaks_the_pin_only_inside_a_transaction(execenv, begin, broken):
    # when
    outcome = await disconnect_in_statement(execenv, begin=begin)
    # then
    assert outcome == broken


@pytest.mark.parametrize("op", ["fetch_all", "execute", "execute_many", "stream"])
@pytest.mark.parametrize("error", [ValueError("boom"), IntegrityError("dup"), DatabaseError("db")])
async def test_an_ordinary_error_inside_a_transaction_does_not_break_the_pin(execenv, op, error):
    # when
    broken = await error_in_transaction(execenv, op, error)
    # then
    assert broken is False


@pytest.mark.parametrize(
    ("steps", "broken"),
    [
        P(["begin", "commit", "cancel"], False, id="cancel-after-commit"),
        P(["begin", "rollback", "cancel"], False, id="cancel-after-rollback"),
        P(["begin_fails", "cancel"], False, id="cancel-after-failed-begin"),
        P(["begin", "commit_fails", "cancel"], False, id="cancel-after-failed-commit"),
        P(["begin_cancelled"], True, id="cancelled-begin"),
        P(["begin", "begin_fails", "cancel"], True, id="failed-second-begin-keeps-the-transaction"),
        P(["begin", "cancel"], True, id="cancel-inside-transaction"),
        P(["begin", "rollback", "begin", "cancel"], True, id="cancel-in-a-second-transaction"),
        P(["cancel"], False, id="cancel-without-transaction"),
    ],
)
async def test_a_cancel_breaks_the_pin_only_while_a_transaction_is_open(execenv, steps, broken):
    # when
    outcome = await broken_after(execenv, steps)
    # then
    assert outcome is broken
