"""Sans-I/O cores of the read operations that are not plain ``alist`` (ADR-0007, ADR-0008 Decision 4).

``count_core`` is ``Query.get_count``, ``aggregation_core`` is ``Query.get_aggregation`` and
``exists_core`` is ``Query.has_results``; the Django-side logic lives in
``asyncopation._django.aggregation`` (ported without its I/O). Each core yields ``Compile`` /
``Execute`` / ``Convert`` requests, so a runner decides how they run.
"""

# Ported from django/db/models/sql/query.py::Query.get_aggregation, Query.get_count, Query.has_results @ 6.1.3
# Changes: the I/O of each is a request (`Execute`, and `Convert` for the row conversion, which must
# run outside `compile_scope`); the query rewrite and the conversion live in `_django/aggregation.py`.

from __future__ import annotations

from typing import Any

from asyncopation._django import aggregation
from asyncopation.engine.compiled import ResultKind
from asyncopation.engine.runner import Compile, Convert, Core, Execute

__all__ = ["aggregation_core", "count_core", "exists_core"]


def aggregation_core(query: Any, aggregate_exprs: dict[str, Any]) -> Core[dict[str, Any]]:
    """``Query.get_aggregation(using, aggregate_exprs)``.

    ``query`` is mutated exactly as Django mutates ``self``: callers pass a clone (``count_core`` does;
    a future ``aaggregate`` must clone ``queryset.query``, as ``QuerySet.aggregate`` does).
    """
    if not aggregate_exprs:
        return {}
    outer_query, elide_empty, empty_set_result = aggregation.prepare_aggregation(
        query, aggregate_exprs
    )
    compiled = yield Compile(outer_query, ResultKind.SINGLE, elide_empty)
    row = yield Execute(compiled)
    # The conversion runs user `from_db_value` code: the runner serves it outside any compile scope.
    result = yield Convert(compiled, outer_query, row, empty_set_result)
    return result


def count_core(query: Any) -> Core[int]:
    """``Query.get_count(using)``: ``COUNT(*)`` over a clone of ``query``."""
    clone, exprs = aggregation.count_query(query)
    result = yield from aggregation_core(clone, exprs)
    return result["__count"]


def exists_core(query: Any) -> Core[bool]:
    """``Query.has_results(using)``: whether the ``LIMIT 1`` form of ``query`` returns a row."""
    compiled = yield Compile(aggregation.exists_query(query), ResultKind.SINGLE)
    row = yield Execute(compiled)
    return bool(row)
