"""The execute stage: run a ``CompiledQuery`` on an executor (ADR-0002 Decision 2)."""

from __future__ import annotations

from typing import Any

from asyncopation.connection.executor import Executor
from asyncopation.engine.compiled import CompiledQuery, ResultKind

__all__ = ["execute_multi", "execute_single"]


async def execute_multi(compiled: CompiledQuery, executor: Executor) -> list[tuple[Any, ...]]:
    """All rows of a MULTI query; an EMPTY query does no I/O.

    Rows are trimmed to ``col_count`` when the query has extra select columns (``extra(select=...)``
    ordering columns), as Django's ``cursor_iter`` does.
    """
    if compiled.kind is ResultKind.EMPTY:
        return []
    rows = await executor.fetch_all(compiled.sql, compiled.params)
    if compiled.has_extra_select:
        count = compiled.col_count
        return [row[:count] for row in rows]
    return rows


async def execute_single(compiled: CompiledQuery, executor: Executor) -> tuple[Any, ...] | None:
    """The first row of a SINGLE query cut to ``col_count`` (``None`` for no row or EMPTY).

    As Django's ``execute_sql(SINGLE)``: the row is ``fetchone()`` trimmed to the compiler's
    ``col_count``; an empty result is returned as it came.
    """
    if compiled.kind is ResultKind.EMPTY:
        return None
    rows = await executor.fetch_all(compiled.sql, compiled.params)
    row = rows[0] if rows else None
    if row:
        return row[: compiled.col_count]
    return row
