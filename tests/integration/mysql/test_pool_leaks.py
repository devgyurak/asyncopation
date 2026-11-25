"""Acquires that time out or are cancelled never leak a connection (ADR-0001 Decision 8)."""

import pytest

from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import LeakOutcome, stress_pool_with_timeouts

pytestmark = MYSQL_FAMILY


@pytest.fixture(
    scope="module",
    params=[
        pytest.param({}, id="plain"),
        pytest.param({"health_checks": True}, id="health-checks"),
        pytest.param({"max_waiting": 20}, id="max-waiting"),
    ],
)
async def outcome(request: pytest.FixtureRequest, django_db_setup: None) -> LeakOutcome:
    """One 200-task run per pool shape, shared by the assertions below."""
    return await stress_pool_with_timeouts(**request.param)


async def test_racing_acquires_leave_nothing_in_use_or_waiting(outcome):
    # when
    stats = outcome.stats
    # then
    assert stats.in_use == 0
    assert stats.waiting == 0


async def test_racing_acquires_leave_no_pool_connection_open_beyond_the_idle_ones(outcome):
    # when
    threads = outcome.threads
    # then
    assert threads == outcome.expected_threads


async def test_racing_acquires_end_only_as_finished_or_timed_out(outcome):
    # when
    unexpected = outcome.unexpected
    # then
    assert unexpected == 0
