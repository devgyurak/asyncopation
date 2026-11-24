"""Async helpers that replace control flow in test modules."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable, Callable


async def collect[T](iterable: AsyncIterable[T]) -> list[T]:
    """Drain an async iterator (replaces `[x async for x in it]` in tests)."""
    return [item async for item in iterable]


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], *, within: float = 5.0, interval: float = 0.05
) -> None:
    """Poll an async predicate until it is true (replaces sleeps in tests)."""
    async with asyncio.timeout(within):
        while not await predicate():  # noqa: ASYNC110 - polling external state (DB/HTTP)
            await asyncio.sleep(interval)
