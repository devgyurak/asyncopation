from dataclasses import FrozenInstanceError

import pytest

from asyncopation.drivers.base import Capabilities, ExecResult, PoolStats
from tests.support.drivers import replace_field, set_attribute


@pytest.mark.parametrize(
    ("value", "attribute"),
    [
        pytest.param(
            Capabilities(
                server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True
            ),
            "server_version",
            id="capabilities",
        ),
        pytest.param(ExecResult(rowcount=1), "rowcount", id="exec-result"),
        pytest.param(PoolStats(in_use=0, idle=1, waiting=0), "in_use", id="pool-stats"),
    ],
)
def test_value_types_are_frozen(value, attribute):
    # when / then
    with pytest.raises(FrozenInstanceError):
        set_attribute(value, attribute, 99)


def test_exec_result_defaults_to_no_lastrowid_and_no_rows():
    # when
    result = ExecResult(rowcount=3)
    # then
    assert result.lastrowid is None
    assert result.rows is None


def test_exec_result_rowcount_may_be_unknown():
    # when
    result = ExecResult(rowcount=-1)
    # then
    assert result.rowcount == -1


def test_capabilities_with_equal_fields_are_equal_and_hashable():
    # given
    left, right = (
        Capabilities(server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True),
        Capabilities(server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True),
    )
    # when
    same = {left, right}
    # then
    assert left == right
    assert len(same) == 1


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"server_version": (16, 0)}, id="server-version"),
        pytest.param({"server_side_cursors": False}, id="server-side-cursors"),
        pytest.param({"atomic_execute_many": False}, id="atomic-execute-many"),
    ],
)
def test_capabilities_differ_by_any_field(changes):
    # given
    base = Capabilities(server_version=(17, 0), server_side_cursors=True, atomic_execute_many=True)
    # when
    changed = replace_field(base, **changes)
    # then
    assert base != changed


def test_pool_stats_equality_compares_all_counters():
    # when / then
    assert PoolStats(in_use=1, idle=2, waiting=3) == PoolStats(in_use=1, idle=2, waiting=3)
    assert PoolStats(in_use=1, idle=2, waiting=3) != PoolStats(in_use=1, idle=2, waiting=4)
