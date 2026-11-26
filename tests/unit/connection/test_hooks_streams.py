"""ADR-0005 Decision 3 (issue #4): a stream is observed as started when it opens and finished
once when it closes, with the rows read as rowcount and only stream failures as its error."""

import asyncio

import pytest

from tests.support.hooks.events import BOTH
from tests.support.hooks.lifetime import global_removed_while_stream_open
from tests.support.hooks.streams import (
    ConsumerError,
    borrowed_stream_manager_without_subscribers,
    cancel_between_chunks,
    cancel_while_reading,
    closed_after_the_block,
    observer_added_while_open,
    stream_left,
)

PINNED = pytest.mark.parametrize("pinned", [False, True], ids=["borrowed", "pinned"])
EVERY_EXIT = pytest.mark.parametrize(
    "how",
    [
        "full",
        "one_chunk",
        "break",
        "consumer_error",
        "generator_close",
        "chunk_error",
        "close_error",
        "chunk_cancelled",
    ],
)


@EVERY_EXIT
@PINNED
async def test_a_stream_emits_exactly_one_started_and_one_finished_event(execenv, how, pinned):
    # when
    observed = await stream_left(execenv, how, pinned=pinned)
    # then
    assert observed.names == BOTH


@PINNED
async def test_a_stream_read_to_the_end_finishes_with_the_total_rowcount(execenv, pinned):
    # when
    observed = await stream_left(execenv, "full", pinned=pinned)
    # then
    assert observed.finished.rowcount == 25


@pytest.mark.parametrize("how", ["one_chunk", "break", "generator_close", "consumer_error"])
@PINNED
async def test_a_stream_left_early_finishes_with_the_rows_read(execenv, how, pinned):
    # when
    observed = await stream_left(execenv, how, pinned=pinned)
    # then
    assert observed.finished.rowcount == 10


@pytest.mark.parametrize("how", ["full", "one_chunk", "break", "generator_close"])
@PINNED
async def test_a_stream_left_normally_finishes_without_error(execenv, how, pinned):
    # when
    observed = await stream_left(execenv, how, pinned=pinned)
    # then
    assert (observed.error, observed.finished.error) == (None, None)


@PINNED
async def test_an_error_of_the_consumer_is_not_the_stream_error(execenv, pinned):
    # when
    observed = await stream_left(execenv, "consumer_error", pinned=pinned)
    # then
    assert isinstance(observed.error, ConsumerError)
    assert observed.finished.error is None


@pytest.mark.parametrize("how", ["chunk_error", "close_error"])
@PINNED
async def test_a_stream_failure_is_the_finished_error(execenv, how, pinned):
    # when
    observed = await stream_left(execenv, how, pinned=pinned)
    # then
    assert observed.finished.error is observed.injected
    assert observed.error is observed.injected


@pytest.mark.parametrize("how", ["chunk_error", "close_error", "chunk_cancelled"])
@PINNED
async def test_a_failed_stream_has_no_rowcount(execenv, how, pinned):
    # when
    observed = await stream_left(execenv, how, pinned=pinned)
    # then
    assert observed.finished.rowcount is None


@PINNED
async def test_a_chunk_read_raising_cancelled_finishes_with_a_cancelled_error(execenv, pinned):
    # when
    observed = await stream_left(execenv, "chunk_cancelled", pinned=pinned)
    # then
    assert isinstance(observed.finished.error, asyncio.CancelledError)


@PINNED
async def test_a_task_cancelled_while_reading_a_stream_finishes_it_with_a_cancelled_error(
    execenv, pinned
):
    # when
    observed = await cancel_while_reading(execenv, pinned=pinned)
    # then
    assert observed.cancelled is True
    assert observed.names == BOTH
    assert isinstance(observed.finished.error, asyncio.CancelledError)


@PINNED
async def test_a_task_cancelled_while_reading_a_stream_finishes_it_without_rowcount(
    execenv, pinned
):
    # when
    observed = await cancel_while_reading(execenv, pinned=pinned)
    # then
    assert observed.finished.rowcount is None


@PINNED
async def test_a_task_cancelled_between_chunks_finishes_the_stream_with_a_cancelled_error(
    execenv, pinned
):
    # when
    observed = await cancel_between_chunks(execenv, pinned=pinned)
    # then
    assert observed.cancelled is True
    assert isinstance(observed.finished.error, asyncio.CancelledError)
    assert observed.finished.rowcount is None


@pytest.mark.parametrize("scope", ["scoped", "global"])
@PINNED
async def test_an_observer_added_while_a_stream_is_open_does_not_see_it_finish(
    execenv, scope, pinned
):
    # when
    first, late = await observer_added_while_open(execenv, scope=scope, pinned=pinned)
    # then
    assert first.names == BOTH
    assert late.events == []


@PINNED
async def test_a_global_observer_removed_while_a_stream_is_open_still_sees_it_finish(
    execenv, pinned
):
    # when
    recorder = await global_removed_while_stream_open(execenv, pinned=pinned)
    # then
    assert recorder.names == BOTH


@pytest.mark.parametrize("in_child", [False, True], ids=["same-task", "other-task"])
@PINNED
async def test_a_stream_closed_after_the_observe_block_still_reaches_its_observer(
    execenv, in_child, pinned
):
    # when
    recorder = await closed_after_the_block(execenv, pinned=pinned, in_child=in_child)
    # then
    assert recorder.names == BOTH
    assert recorder.only_finished.rowcount == 10


async def test_a_stream_without_any_observer_still_reads_every_row(execenv):
    # when
    chunks = await borrowed_stream_manager_without_subscribers(execenv)
    # then
    assert len(chunks) == 3
