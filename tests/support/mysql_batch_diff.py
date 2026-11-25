"""`execute_many` against Django's sync `executemany` (mysqlclient) on the scratch table.

Outcome = (rowcount or error, rows left, number of INSERT/REPLACE/UPDATE/DELETE statements the
server ran). The statement count shows how a batch was rewritten and chunked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async

from tests.support.mysql_env import admin_query, open_my_driver
from tests.support.mysql_reference import error_name, real_wrapper

COUNTERS = "('Com_insert', 'Com_insert_select', 'Com_replace', 'Com_update', 'Com_delete')"
COUNT_SQL = f"SHOW SESSION STATUS WHERE Variable_name IN {COUNTERS}"
ROWS_SQL = "SELECT name, qty FROM {t} ORDER BY name"
SEED_SQL = "INSERT INTO {t} (name, qty) VALUES ('a', 0)"


@dataclass(frozen=True)
class BatchOutcome:
    result: Any  # rowcount, or the error name
    rows: list[Any]
    statements: int


@dataclass(frozen=True)
class BatchComparison:
    async_outcome: BatchOutcome
    sync_outcome: BatchOutcome


def _total(rows: list[Any]) -> int:
    return sum(int(row[1]) for row in rows)


async def _reset(table: str) -> None:
    await admin_query(f"DELETE FROM {table}")
    await admin_query(SEED_SQL.format(t=table))


async def _async_outcome(table: str, template: str, batch: list[Any]) -> BatchOutcome:
    sql = template.format(t=table)
    async with open_my_driver() as driver, driver.connection() as conn:
        before = _total(await conn.fetch_all(COUNT_SQL, None))
        try:
            result: Any = (await conn.execute_many(sql, batch)).rowcount
        except Exception as exc:
            result = error_name(exc)
        after = _total(await conn.fetch_all(COUNT_SQL, None))
    rows = [tuple(r) for r in await admin_query(ROWS_SQL.format(t=table))]
    return BatchOutcome(result, rows, after - before)


async def _sync_outcome(table: str, template: str, batch: list[Any]) -> BatchOutcome:
    sql = template.format(t=table)

    def run() -> tuple[Any, int]:
        wrapper = real_wrapper()
        try:
            with wrapper.cursor() as cursor:
                cursor.execute(COUNT_SQL)
                before = _total(list(cursor.fetchall()))
                try:
                    cursor.executemany(sql, batch)
                    result: Any = cursor.rowcount
                except Exception as exc:
                    result = error_name(exc)
                cursor.execute(COUNT_SQL)
                after = _total(list(cursor.fetchall()))
            return result, after - before
        finally:
            wrapper.close()

    result, statements = await sync_to_async(run)()
    rows = [tuple(r) for r in await admin_query(ROWS_SQL.format(t=table))]
    return BatchOutcome(result, rows, statements)


async def batch_vs_sync(table: str, template: str, batch: Any) -> BatchComparison:
    """Run the batch through the driver and through Django's sync cursor on the same seeded table.
    `batch` may be a callable returning the batch (a generator can be consumed only once)."""
    await _reset(table)
    ours = await _async_outcome(table, template, batch() if callable(batch) else batch)
    await _reset(table)
    theirs = await _sync_outcome(table, template, batch() if callable(batch) else batch)
    return BatchComparison(ours, theirs)


def named_rows(count: int, width: int = 8) -> list[tuple[str, int]]:
    return [(f"n{index:0{width}d}", 1) for index in range(count)]


def rows_ending_with_a_duplicate(width: int) -> list[tuple[str, int]]:
    """7000 named rows of the given width and, last, a copy of the first one."""
    return [*named_rows(7000, width), ("n" + "0" * width, 1)]


def one_shot_generator_of_two_rows() -> Any:
    return (row for row in [("x", 1), ("y", 2)])
