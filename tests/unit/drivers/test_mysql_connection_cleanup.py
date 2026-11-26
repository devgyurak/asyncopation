"""MySQLConnection bookkeeping against fakes (no server)."""

import asyncio

from tests.support.mysql_fakes import open_cursors_after_a_cancelled_close


async def test_a_cancel_while_draining_a_stream_cursor_forgets_it_and_discards_the_connection():
    # when
    error, open_cursors, discarded = await open_cursors_after_a_cancelled_close()
    # then
    assert isinstance(error, asyncio.CancelledError)
    assert open_cursors == []
    assert discarded is True
