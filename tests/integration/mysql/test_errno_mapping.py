"""Server errnos map to the Django class mysqlclient gives them (stored procedure SIGNAL)."""

import pytest

from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_errno import (
    CHUNKS,
    classes_for,
    connection_replaced_after,
    sweep_differences,
)

pytestmark = MYSQL_FAMILY


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        pytest.param(1166, "OperationalError", id="1166-wrong-column-name"),
        pytest.param(1169, "IntegrityError", id="1169-dup-unique"),
        pytest.param(1263, "OperationalError", id="1263-data-truncated"),
        pytest.param(1364, "IntegrityError", id="1364-no-default"),
        pytest.param(1366, "OperationalError", id="1366-wrong-value-for-field"),
        pytest.param(1367, "OperationalError", id="1367-illegal-value"),
        pytest.param(2014, "ProgrammingError", id="2014-commands-out-of-sync"),
    ],
)
async def test_the_errnos_asyncmy_classifies_differently_follow_mysqlclient(
    signal_procedure, code, expected
):
    # when
    ours, theirs = await classes_for(code)
    # then
    assert (ours, theirs) == (expected, expected)


@pytest.mark.parametrize(("start", "stop"), CHUNKS, ids=lambda value: str(value))
async def test_every_errno_in_a_range_maps_like_mysqlclient(signal_procedure, start, stop):
    # when
    differing = await sweep_differences(start, stop)
    # then
    assert differing == {}


async def test_errno_2014_commands_out_of_sync_discards_the_connection(signal_procedure):
    # when
    error, before, after = await connection_replaced_after(2014)
    # then
    assert error == "ProgrammingError"
    assert before != after


async def test_an_ordinary_server_error_keeps_the_connection(signal_procedure):
    # when
    error, before, after = await connection_replaced_after(1205)
    # then
    assert error == "OperationalError"
    assert before == after
