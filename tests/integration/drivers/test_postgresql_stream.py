"""Streams: chunking, server-side cursor options and cleanup."""

from tests.support.drivers import (
    POSTGRES_ONLY,
    flatten,
    holdable_flags,
    observe_failed_stream,
    observe_stream,
    open_cursors,
    scrollable_flags,
    sizes,
    stream_chunks,
)

pytestmark = POSTGRES_ONLY

SERIES = "SELECT g FROM generate_series(1, 10) g ORDER BY g"


async def test_stream_yields_chunks_of_at_most_chunk_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 4)
    # then
    assert sizes(chunks) == [4, 4, 2]


async def test_stream_chunks_concatenate_to_fetch_all_rows(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 3)
        rows = await conn.fetch_all(SERIES, None)
    # then
    assert flatten(chunks) == rows


async def test_stream_with_chunk_larger_than_result_yields_one_chunk(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, SERIES, None, 100)
    # then
    assert sizes(chunks) == [10]


async def test_stream_without_rows_yields_no_chunks(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(conn, "SELECT 1 WHERE false", None, 5)
    # then
    assert chunks == []


async def test_stream_binds_parameters(driver):
    # given
    async with driver.connection() as conn:
        # when
        chunks = await stream_chunks(
            conn, "SELECT g FROM generate_series(1, %s) g ORDER BY g", (5,), 2
        )
    # then
    assert flatten(chunks) == [(1,), (2,), (3,), (4,), (5,)]


async def test_stream_declares_non_scrollable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2)
    # then
    assert scrollable_flags(seen.cursors_open) == [False]


async def test_stream_in_autocommit_declares_holdable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2)
    # then
    assert holdable_flags(seen.cursors_open) == [True]


async def test_stream_inside_transaction_declares_non_holdable_cursor(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, transactional=True)
    # then
    assert holdable_flags(seen.cursors_open) == [False]


async def test_stream_exit_closes_cursor_after_partial_read(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, reads=1)
    # then
    assert len(seen.cursors_open) == 1
    assert seen.cursors_after == []


async def test_stream_exit_closes_cursor_inside_transaction(driver):
    # given
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, SERIES, None, 2, transactional=True)
    # then
    assert seen.cursors_after == []


async def test_stream_closes_cursor_when_body_raises(driver):
    # given
    async with driver.connection() as conn:
        # when
        outcome = await observe_failed_stream(conn, SERIES, 2)
    # then
    assert isinstance(outcome.error, RuntimeError)
    assert outcome.cursors_after == []


async def test_stream_leaves_connection_reusable(driver):
    # given
    async with driver.connection() as conn:
        await observe_stream(conn, SERIES, None, 2)
        # when
        rows = await conn.fetch_all("SELECT 1", None)
    # then
    assert rows == [(1,)]


async def test_stream_binding_mode_selects_cursor_statement_text(driver, binding):
    # given
    sql = "SELECT g FROM generate_series(1, %s) g"
    async with driver.connection() as conn:
        # when
        seen = await observe_stream(conn, sql, (5,), 2)
    # then
    assert ("$1" in seen.cursors_open[0].statement) is binding.server_side


async def test_no_cursor_is_open_on_a_fresh_connection(driver):
    # given
    async with driver.connection() as conn:
        # when
        cursors = await open_cursors(conn)
    # then
    assert cursors == []
