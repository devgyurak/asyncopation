"""Streams: chunking, early exit, cancellation, buffered fallback."""

import pytest
from django.db import InterfaceError

from asyncopation.drivers.base import PoolStats
from tests.support.mysql_env import MYSQL_FAMILY
from tests.support.mysql_scenarios import (
    EVERY_ROW,
    abandon_stream,
    buffered_begin_and_commit_inside_a_stream,
    cancel_during_sleep,
    chunk_sizes,
    django_class_of_late_error,
    error_during_fetch_in_transaction,
    flatten,
    leave_slow_stream,
    nested_fetch_in_buffered_stream,
    nested_statement_error,
    release_with_open_stream,
    series_chunks,
    shrink_recovery_timeout,
    stale_stream_iterator_error,
    stream_body_error_inside_a_transaction,
    stream_chunks,
    stream_early_exit,
)

pytestmark = MYSQL_FAMILY


@pytest.mark.parametrize("chunk", [1, 7, 100, 1000])
async def test_stream_chunks_hold_at_most_chunk_rows(driver, chunk):
    # when
    chunks = await series_chunks(driver, chunk)
    # then
    assert max(chunk_sizes(chunks)) <= chunk


@pytest.mark.parametrize("chunk", [1, 7, 100, 1000])
async def test_stream_yields_every_row_in_order(driver, chunk):
    # when
    chunks = await series_chunks(driver, chunk)
    # then
    assert flatten(chunks) == EVERY_ROW


async def test_stream_of_an_empty_result_yields_no_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, "SELECT 1 FROM (SELECT 1) t WHERE 1 = 0", 10)
    # then
    assert flatten(chunks) == []


async def test_leaving_a_stream_early_leaves_the_connection_usable(driver):
    # when
    outcome = await stream_early_exit(driver)
    # then
    assert outcome.next_rows == [(1,)]


async def test_leaving_a_stream_early_keeps_the_same_connection(driver):
    # when
    outcome = await stream_early_exit(driver)
    # then
    assert outcome.same_connection is True


async def test_leaving_a_stream_early_emits_no_warning(driver):
    # when
    outcome = await stream_early_exit(driver)
    # then
    assert outcome.warnings == []


async def test_leaving_a_stream_early_has_read_only_the_first_chunk(driver):
    # when
    outcome = await stream_early_exit(driver, chunk=10)
    # then
    assert outcome.first_chunk == 10


async def test_cancelling_a_stream_discards_the_connection(single_driver):
    # when
    outcome = await cancel_during_sleep(single_driver, streaming=True)
    # then
    assert outcome.cancelled is True
    assert outcome.next_id != outcome.held_id


async def test_cancelling_a_stream_leaves_nothing_in_use_and_the_pool_refills(single_driver):
    # when
    outcome = await cancel_during_sleep(single_driver, streaming=True)
    # then
    assert outcome.after.in_use == 0
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)


async def test_cancelling_a_stream_leaves_a_healthy_connection_for_the_next_borrower(
    single_driver,
):
    # when
    outcome = await cancel_during_sleep(single_driver, streaming=True)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.next_autocommit == 1


@pytest.mark.parametrize("chunk", [1, 7, 100, 1000])
async def test_buffered_streams_hold_at_most_chunk_rows(no_cursor_driver, chunk):
    # when
    chunks = await series_chunks(no_cursor_driver, chunk)
    # then
    assert max(chunk_sizes(chunks)) <= chunk


@pytest.mark.parametrize("chunk", [1, 7, 100, 1000])
async def test_buffered_streams_yield_every_row_in_order(no_cursor_driver, chunk):
    # when
    chunks = await series_chunks(no_cursor_driver, chunk)
    # then
    assert flatten(chunks) == EVERY_ROW


async def test_disabling_server_side_cursors_is_reported_in_the_capabilities(no_cursor_driver):
    # when
    capabilities = no_cursor_driver.capabilities
    # then
    assert capabilities.server_side_cursors is False


@pytest.mark.parametrize(
    "operation", ["fetch_all", "execute", "execute_many", "stream", "begin", "commit"]
)
async def test_a_statement_while_an_unbuffered_stream_is_open_raises_interface_error(
    driver, operation
):
    # when
    error = await nested_statement_error(driver, operation)
    # then
    assert type(error) is InterfaceError


async def test_a_statement_inside_a_buffered_stream_works(no_cursor_driver):
    # when
    nested, streamed = await nested_fetch_in_buffered_stream(no_cursor_driver)
    # then
    assert nested == [(1,)]
    assert streamed == len(EVERY_ROW)


@pytest.mark.parametrize("cancel", [False, True], ids=["body-raises", "body-cancelled"])
async def test_a_stream_whose_body_fails_discards_the_connection_without_draining(
    single_driver, monkeypatch, cancel
):
    # given
    shrink_recovery_timeout(monkeypatch, 10.0)  # a drain would take 10 s, closing ~1 s
    # when
    outcome = await abandon_stream(single_driver, cancel=cancel, slow=True)
    # then
    assert outcome.next_id != outcome.held_id
    assert outcome.elapsed < 3.0


@pytest.mark.parametrize("cancel", [False, True], ids=["body-raises", "body-cancelled"])
async def test_a_stream_whose_body_fails_leaves_the_pool_usable(single_driver, cancel):
    # when
    outcome = await abandon_stream(single_driver, cancel=cancel)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)


async def test_a_stream_whose_body_raises_propagates_the_body_error(single_driver):
    # when
    outcome = await abandon_stream(single_driver, cancel=False)
    # then
    assert type(outcome.error) is ValueError


async def test_releasing_a_connection_with_an_open_stream_leaves_it_reusable(single_driver):
    # when
    outcome = await release_with_open_stream(single_driver)
    # then
    assert outcome.next_rows == [(1,)]
    assert outcome.warnings == []
    assert outcome.after == PoolStats(in_use=0, idle=1, waiting=0)


async def test_a_stale_stream_iterator_raises_interface_error(single_driver):
    # when
    error = await stale_stream_iterator_error(single_driver)
    # then
    assert type(error) is InterfaceError


async def test_a_server_error_between_chunks_inside_a_transaction_maps_like_django(
    single_driver, readonly_db
):
    # when
    error = await error_during_fetch_in_transaction(single_driver)
    expected = await django_class_of_late_error()
    # then
    assert expected is not None
    assert type(error) is expected


async def test_begin_and_commit_inside_a_buffered_stream_work(no_cursor_driver):
    # when
    errors = await buffered_begin_and_commit_inside_a_stream(no_cursor_driver)
    # then
    assert errors == [None, None]


async def test_a_stream_body_error_inside_a_transaction_keeps_the_transaction(
    single_driver, scratch
):
    # when
    outcome = await stream_body_error_inside_a_transaction(single_driver, scratch)
    # then
    assert type(outcome.body_error) is ValueError
    assert outcome.same_connection is True
    assert outcome.committed == ["a"]


@pytest.mark.parametrize("cancel", [False, True], ids=["body-raises", "body-cancelled"])
async def test_a_buffered_stream_whose_body_fails_keeps_the_connection(no_cursor_driver, cancel):
    # when
    outcome = await abandon_stream(no_cursor_driver, cancel=cancel)
    # then
    assert outcome.next_id == outcome.held_id
    assert outcome.next_rows == [(1,)]


async def test_cancelling_a_stream_inside_a_transaction_discards_the_connection(single_driver):
    # when
    outcome = await abandon_stream(single_driver, cancel=True, transactional=True)
    # then
    assert outcome.next_id != outcome.held_id
    assert outcome.next_autocommit == 1
    assert outcome.elapsed < 3
    assert outcome.final == PoolStats(in_use=0, idle=1, waiting=0)


@pytest.mark.parametrize("via_release", [False, True], ids=["break", "released-open"])
async def test_a_slow_stream_left_early_is_drained_only_up_to_the_bound_then_discarded(
    single_driver, monkeypatch, via_release
):
    # given
    shrink_recovery_timeout(monkeypatch, 0.5)
    # when
    outcome = await leave_slow_stream(single_driver, via_release=via_release)
    # then
    assert 0.4 <= outcome.elapsed < 3.0
    assert outcome.same_connection is False
    assert outcome.next_rows == [(1,)]
