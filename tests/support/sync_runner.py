"""A synchronous runner for the sans-I/O cores (ADR-0007 Validation).

It answers `Compile` with Django's own compiler and `Execute` with a cursor of Django's real
connection, so the same core can run beside the async runner and beside Django's own
`Query.get_count` / `has_results` / `get_aggregation`. Tests only: it uses Django's sync
connection, which `src/` never does.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import EmptyResultSet
from django.db import connections

from asyncopation._django import aggregation
from asyncopation.engine.compiled import CompiledQuery, ResultKind
from asyncopation.engine.runner import Compile, Convert, Core


def _compile(request: Compile, alias: str) -> CompiledQuery:
    compiler = request.query.get_compiler(using=alias, elide_empty=request.elide_empty)
    try:
        sql, params = compiler.as_sql()
        if not sql:
            raise EmptyResultSet
    except EmptyResultSet:
        return CompiledQuery(alias, compiler, "", (), ResultKind.EMPTY, 0, False)
    return CompiledQuery(
        alias,
        compiler,
        sql,
        tuple(params),
        request.kind,
        compiler.col_count,
        bool(getattr(compiler, "has_extra_select", False)),
    )


def _execute(compiled: CompiledQuery) -> Any:
    if compiled.kind is ResultKind.EMPTY:
        return None
    with connections[compiled.alias].cursor() as cursor:
        cursor.execute(compiled.sql, compiled.params)
        row = cursor.fetchone()
    return row[: compiled.col_count] if row else row


def _convert(request: Convert) -> Any:
    return aggregation.finish_aggregation(
        request.compiled.compiler, request.outer_query, request.row, request.empty_set_result
    )


def _serve(request: Any, alias: str) -> Any:
    if isinstance(request, Compile):
        return _compile(request, alias)
    if isinstance(request, Convert):
        return _convert(request)
    return _execute(request.compiled)


def run_core_sync[T](core: Core[T], alias: str) -> T:
    """Drive `core` to completion on Django's sync connection; errors are thrown into the core."""
    try:
        request = next(core)
        while True:
            try:
                answer = _serve(request, alias)
            except Exception as exc:
                request = core.throw(exc)
            else:
                request = core.send(answer)
    except StopIteration as stop:
        return stop.value
