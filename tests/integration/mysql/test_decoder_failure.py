"""A decoder failing while a result is read must not poison the pooled connection."""

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import decoder_failure

pytestmark = MYSQL_FAMILY


async def test_a_time_beyond_a_day_raises_the_decoders_value_error(single_driver):
    # when
    outcome = await decoder_failure(single_driver)
    # then
    assert type(outcome.error) is ValueError


async def test_the_connection_is_discarded_after_a_decoder_failure(single_driver):
    # when
    outcome = await decoder_failure(single_driver)
    # then
    assert outcome.next_id != outcome.held_id


async def test_the_next_borrower_after_a_decoder_failure_runs_a_query_and_the_pool_refills(
    single_driver,
):
    # when
    outcome = await decoder_failure(single_driver)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)
