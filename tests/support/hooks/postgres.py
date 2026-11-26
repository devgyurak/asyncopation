"""Observed statements and streams on a real PostgreSQL connection (the `spy` fixture)."""

from __future__ import annotations

from typing import Any

from tests.support.aio import collect
from tests.support.executors.scenarios import executor_module
from tests.support.hooks.scenarios import Observed, observed

ALIAS = "default"
THREE = "SELECT g FROM generate_series(1, 3) g"
TWENTY_FIVE = "SELECT g FROM generate_series(1, 25) g"


async def observed_real_fetch_all() -> Observed:
    async def run() -> Any:
        return await executor_module().BorrowedExecutor(ALIAS).fetch_all(THREE, None)

    return await observed(run)


async def observed_real_stream(*, pinned: bool) -> Observed:
    """Stream 25 rows in chunks of 10 and read them all."""

    async def run() -> Any:
        if pinned:
            async with executor_module().pin(ALIAS) as executor:
                return await _stream_all(executor)
        return await _stream_all(executor_module().BorrowedExecutor(ALIAS))

    return await observed(run)


async def _stream_all(executor: Any) -> list[Any]:
    async with executor.stream(TWENTY_FIVE, None, 10) as chunks:
        return await collect(chunks)


async def observed_real_transaction() -> Observed:
    """BEGIN, SAVEPOINT sp, RELEASE SAVEPOINT sp and COMMIT on a pinned real connection."""

    async def run() -> None:
        async with executor_module().pin(ALIAS) as executor:
            await executor.begin()
            await executor.savepoint("sp")
            await executor.release_savepoint("sp")
            await executor.commit()

    return await observed(run)


async def real_stream_with_consumer_error(*, pinned: bool) -> Observed:
    """The consumer's code raises after one chunk of a real stream."""
    from tests.support.hooks.streams import ConsumerError

    async def use(executor: Any) -> None:
        async with executor.stream(TWENTY_FIVE, None, 10) as chunks:
            await anext(chunks)
            raise ConsumerError("the consumer failed")

    async def run() -> None:
        if pinned:
            async with executor_module().pin(ALIAS) as executor:
                await use(executor)
        else:
            await use(executor_module().BorrowedExecutor(ALIAS))

    return await observed(run)
