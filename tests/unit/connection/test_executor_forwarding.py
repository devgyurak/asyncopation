"""Every statement kind hands its SQL and parameters to the driver and returns what it gave."""

import pytest

from asyncopation.drivers.base import ExecResult
from tests.support.executors.failures import forward_statement

P = pytest.param
EXECUTORS = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])


@EXECUTORS
@pytest.mark.parametrize(
    ("op", "expected"),
    [
        P("fetch_all", [(1,), (2,)], id="fetch_all"),
        P("execute", ExecResult(rowcount=3), id="execute"),
        P("execute_many", ExecResult(rowcount=5), id="execute_many"),
        P("stream", [[(1,), (2,)], [(3,)]], id="stream"),
    ],
)
async def test_the_statement_returns_what_the_driver_gave(execenv, pinned, op, expected):
    # when
    outcome = await forward_statement(execenv, pinned=pinned, op=op)
    # then
    assert outcome.result == expected


@EXECUTORS
@pytest.mark.parametrize(
    ("op", "logged"),
    [
        P("fetch_all", ("fetch_all", "SELECT %s", (5,)), id="fetch_all"),
        P("execute", ("execute", "SELECT %s", (5,)), id="execute"),
        P("execute_many", ("execute_many", "SELECT %s", [(5,), (6,)]), id="execute_many"),
        P("stream", ("stream", "SELECT %s", (5,)), id="stream"),
    ],
)
async def test_the_statement_forwards_its_sql_and_parameters_unchanged(execenv, pinned, op, logged):
    # when
    outcome = await forward_statement(execenv, pinned=pinned, op=op)
    # then
    assert outcome.statement == logged
