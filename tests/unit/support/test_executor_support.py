"""The gated recording driver the executor tests rely on."""

import asyncio

from tests.support.executors.fakes import opened_gated_driver
from tests.support.executors.ops import call_with, in_gather_child, in_taskgroup_child, raised_by


async def test_an_unqueued_statement_returns_one_row_instead_of_failing():
    # given
    driver = await opened_gated_driver()
    # when
    async with driver.connection() as connection:
        rows = await connection.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


async def test_queued_results_are_served_before_the_default_row():
    # given
    driver = await opened_gated_driver()
    driver.queue([(5,)])
    # when
    async with driver.connection() as connection:
        rows = [await connection.fetch_all("a", None), await connection.fetch_all("b", None)]
    # then
    assert rows == [[(5,)], [(1,)]]


async def test_every_borrow_is_counted_and_released():
    # given
    driver = await opened_gated_driver()
    # when
    async with driver.connection():
        pass
    async with driver.connection():
        pass
    # then
    assert (driver.borrows, driver.released) == (2, [True, True])


async def test_a_held_statement_waits_at_the_gate_until_released():
    # given
    driver = await opened_gated_driver()
    hold = driver.hold_statements()
    async with driver.connection() as connection:
        task = asyncio.create_task(connection.fetch_all("SELECT 1", None))
        # when
        await hold.wait_entered()
        waiting = task.done()
        hold.release()
        rows = await task
    # then
    assert (waiting, rows) == (False, [(1,)])


async def test_the_during_hook_runs_inside_the_statement():
    # given
    driver = await opened_gated_driver()
    calls = []

    async def during():
        calls.append(driver.statement_calls)

    driver.during = during
    # when
    async with driver.connection() as connection:
        await connection.fetch_all("SELECT 1", None)
    # then
    assert calls == [1]


async def test_the_recording_log_still_sees_the_statements():
    # given
    driver = await opened_gated_driver()
    # when
    async with driver.connection() as connection:
        await connection.begin()
        await connection.execute("UPDATE t SET x = 1", None)
    # then
    assert driver.kinds() == ["begin", "execute", "rollback"]


async def test_gather_helper_raises_the_childs_error():
    # given
    async def boom():
        raise KeyError("x")

    # when
    error = await raised_by(in_gather_child(boom()))
    # then
    assert isinstance(error, KeyError)


async def test_taskgroup_helper_unwraps_a_single_error():
    # given
    async def boom():
        raise KeyError("x")

    # when
    error = await raised_by(in_taskgroup_child(boom()))
    # then
    assert isinstance(error, KeyError)


async def test_a_queued_begin_fault_is_raised_once():
    # given
    driver = await opened_gated_driver()
    driver.fail_next("begin", KeyError("x"))
    # when
    async with driver.connection() as connection:
        first = await raised_by(connection.begin())
        second = await raised_by(connection.begin())
    # then
    assert (type(first), second) == (KeyError, None)


async def test_a_queued_chunk_fault_is_raised_by_the_next_read():
    # given
    driver = await opened_gated_driver()
    driver.fail_next("chunk", KeyError("x"))
    # when
    async with driver.connection() as connection, connection.stream("SELECT 1", None, 1) as chunks:
        error = await raised_by(anext(chunks))
    # then
    assert isinstance(error, KeyError)


async def test_a_queued_stream_close_fault_is_raised_when_the_stream_ends():
    # given
    driver = await opened_gated_driver()
    driver.fail_next("stream_close", KeyError("x"))
    # when
    async with driver.connection() as connection:
        error = await raised_by(call_with(connection, "stream", "SELECT 1", None))
        open_after = driver.streams_open
    # then
    assert isinstance(error, KeyError)
    assert open_after == 0


async def test_a_held_stream_close_waits_until_released():
    # given
    driver = await opened_gated_driver()
    hold = driver.hold_stream_closes()
    async with driver.connection() as connection:
        task = asyncio.create_task(call_with(connection, "stream", "SELECT 1", None))
        # when
        await hold.wait_entered()
        waiting = task.done()
        hold.release()
        chunks = await task
    # then
    assert (waiting, chunks) == (False, [[(1,)]])


async def test_a_borrow_error_is_raised_before_a_connection_is_handed_out():
    # given
    driver = await opened_gated_driver()
    driver.borrow_error = KeyError("pool")
    # when
    error = await raised_by(driver.connection().__aenter__())
    # then
    assert isinstance(error, KeyError)
