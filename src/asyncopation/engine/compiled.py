"""The compile stage: a QuerySet becomes a frozen ``CompiledQuery`` (ADR-0002 Decision 2).

Pure and synchronous, no I/O. It runs inside ``compile_scope`` so Django's ``get_compiler`` and
``as_sql`` see the compile-only wrapper, and it calls ``as_sql()`` exactly once because that call
mutates compiler state (``select``, ``klass_info``, ``col_count``...); the compiler instance travels
with the ``CompiledQuery`` so the materializer reads that same state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from django.core.exceptions import EmptyResultSet

from asyncopation._django import query as dq
from asyncopation.compile.scope import compile_scope

__all__ = ["CompiledQuery", "ResultKind", "compile_query"]


_NAMED_KINDS = frozenset({dq.IterableKind.VALUES, dq.IterableKind.NAMED})


class ResultKind(Enum):
    """What the execute stage must do. ``SINGLE`` and ``ROWCOUNT`` arrive with their consumers."""

    MULTI = "multi"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    alias: str
    compiler: Any
    sql: str
    params: tuple[Any, ...]
    kind: ResultKind
    col_count: int
    has_extra_select: bool
    values_names: tuple[str, ...] = ()


def compile_query(queryset: Any, wrapper: Any, *, alias: str, autocommit: bool) -> CompiledQuery:
    """Compile ``queryset`` for MULTI evaluation on ``alias``.

    ``alias`` is the one the caller resolved (``queryset.db`` runs the database routers, so it is
    read once per evaluation and passed on, as Django's iterables do).

    ``EmptyResultSet`` or empty SQL gives ``ResultKind.EMPTY``: nothing is executed (Django's
    ``execute_sql`` returns an empty iterator for it). ``autocommit`` is ``not executor.pinned``.
    """
    query = queryset.query
    # Read before as_sql(): compiling a combined query adds `__orderbycol<N>` annotations (ADR-0002).
    names = tuple(dq.values_names(query)) if dq.iterable_kind(queryset) in _NAMED_KINDS else ()
    with compile_scope(alias, wrapper, autocommit=autocommit):
        compiler = query.get_compiler(using=alias)
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
            ResultKind.MULTI,
            dq.col_count(compiler),
            dq.has_extra_select(compiler),
            names,
        )
