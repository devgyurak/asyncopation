"""Helpers that open several streams on one pinned executor."""

from __future__ import annotations

from typing import Any

from tests.support.executors.scenarios import SELECT


async def open_streams_past_the_busy_guard(executor: Any, count: int) -> None:
    """Enter `count` streams on one pin without leaving any.

    Through the public API only one stream can be open at a time (the executor is busy), so the
    busy guard is lifted before each one to reach the defence in depth of the pin exit.
    """
    for _ in range(count):
        executor._busy = False
        await executor.stream(SELECT, None, 1).__aenter__()
