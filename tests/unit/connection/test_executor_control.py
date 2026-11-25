"""Every call to a connection - statements, streams and transaction control - passes `_run`, and
the ADR-0005 event kind of each is fixed (Decision 2: savepoints are queries)."""

import pytest

from asyncopation.connection.executor import EVENT_KINDS
from tests.support.executors.control import (
    connection_methods,
    kinds_seen_by_run,
    logged_kinds_of_a_transaction,
    transaction_controls_of_borrowed_executor,
)
from tests.support.executors.ops import CONTROLS, STATEMENTS


def test_every_connection_call_has_an_event_kind():
    # when
    covered = set(EVENT_KINDS)
    # then
    assert covered == connection_methods()


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("fetch_all", "query"),
        ("execute", "query"),
        ("execute_many", "query"),
        ("stream", "query"),
        ("savepoint", "query"),
        ("release_savepoint", "query"),
        ("rollback_to_savepoint", "query"),
        ("begin", "transaction"),
        ("commit", "transaction"),
        ("rollback", "transaction"),
    ],
)
def test_a_connection_call_maps_to_its_adr_0005_event_kind(name, kind):
    # when
    found = EVENT_KINDS[name]
    # then
    assert found == kind


@pytest.mark.parametrize("op", [*STATEMENTS, *CONTROLS])
async def test_a_pinned_operation_passes_the_choke_point_once(execenv, monkeypatch, op):
    # when
    seen = await kinds_seen_by_run(monkeypatch, op, pinned=True)
    # then
    assert seen == [op]


@pytest.mark.parametrize("op", STATEMENTS)
async def test_a_borrowed_statement_passes_the_choke_point_once(execenv, monkeypatch, op):
    # when
    seen = await kinds_seen_by_run(monkeypatch, op, pinned=False)
    # then
    assert seen == [op]


async def test_a_transaction_made_of_control_calls_reaches_the_driver_in_order(execenv):
    # when
    kinds = await logged_kinds_of_a_transaction(execenv)
    # then
    assert kinds == [
        "begin",
        "savepoint",
        "rollback_to_savepoint",
        "release_savepoint",
        "commit",
    ]


def test_a_borrowed_executor_exposes_no_transaction_control():
    # when
    exposed = transaction_controls_of_borrowed_executor()
    # then
    assert exposed == set()
