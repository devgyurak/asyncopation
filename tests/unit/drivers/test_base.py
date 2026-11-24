from dataclasses import FrozenInstanceError

import pytest

from asyncopation.drivers.base import Capabilities, ExecResult, PoolStats
from tests.support.drivers import set_attribute


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
