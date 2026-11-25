"""The per-test teardown check itself: it must be able to fail."""

import pytest

from tests.support.lifecycle.checks import teardown_lifecycle
from tests.support.lifecycle.loops import leave_open_in_other_loop


async def test_teardown_check_fails_when_a_driver_is_left_open_in_another_loop(configured):
    # given
    await leave_open_in_other_loop("default")
    # when / then
    with pytest.raises(AssertionError, match="left open"):
        await teardown_lifecycle()


async def test_teardown_check_closes_a_driver_the_current_loop_left_open(opened):
    # when
    await teardown_lifecycle()
    # then
    assert opened.recorder.closes == 1
