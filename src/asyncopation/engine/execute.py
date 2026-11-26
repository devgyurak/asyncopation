"""The execute stage: run a ``CompiledQuery`` on an executor (ADR-0002 Decision 2)."""

from __future__ import annotations

from typing import Any

from asyncopation.connection.executor import Executor
from asyncopation.engine.compiled import CompiledQuery, ResultKind

__all__ = ["execute_multi"]


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
