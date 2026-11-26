"""Read operations over a QuerySet: ``get``, ``first``, ``count``, ``exists`` (ADR-0002, ADR-0007).

Each follows Django 6.1's ``QuerySet`` method of the same name; the I/O goes through
``evaluate_list`` (rows) or a sans-I/O core on ``run_core`` (aggregates).
"""

# Ported from django/db/models/query.py::QuerySet.get, QuerySet.first, QuerySet.count, QuerySet.exists @ 6.1.3
# Changes: evaluation goes through evaluate_list / the aggregation cores; `connections[...].features`
# is read from the compile-only wrapper of the open alias (dq.supports_select_for_update_with_limit).

from __future__ import annotations

from typing import Any

from asyncopation._django import query as dq
from asyncopation.connection import lifecycle
from asyncopation.engine.cores import count_core, exists_core
from asyncopation.engine.evaluate import evaluate_list, resolve_alias
from asyncopation.engine.runner import run_core

__all__ = ["evaluate_count", "evaluate_exists", "evaluate_first", "evaluate_get"]


async def evaluate_get(queryset: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """``QuerySet.get(*args, **kwargs)``: the one matching object, or the Django errors."""
    clone = dq.get_clone(queryset, args, kwargs)
    # Resolved here only when the decision below needs it; evaluate_list resolves it once.
    alias: str | None = None
    limit = None
    if dq.select_for_update(clone):
        # Django reads `connections[alias].features` here; the compile-only wrapper carries the same
        # features, once the alias is open. The router is asked once for the alias and the answer is
        # reused by evaluate_list, where Django asks twice: intentional (ADR-0006, Decision 2).
        alias = resolve_alias(clone)
        await lifecycle.ensure_driver(alias)
        if dq.supports_select_for_update_with_limit(lifecycle.compile_wrapper(alias)):
            limit = dq.MAX_GET_RESULTS
    else:
        limit = dq.MAX_GET_RESULTS
    if limit is not None:
        dq.limit_results(clone, limit)
    rows = await evaluate_list(clone, alias=alias)
    num = len(rows)
    if num == 1:
        return rows[0]
    model = queryset.model
    if not num:
        raise model.DoesNotExist(f"{model._meta.object_name} matching query does not exist.")
    returned = num if not limit or num < limit else f"more than {limit - 1}"
    raise model.MultipleObjectsReturned(
        f"get() returned more than one {model._meta.object_name} -- it returned {returned}!"
    )


async def evaluate_first(queryset: Any) -> Any:
    """``QuerySet.first()``: the first object (by ``pk`` when unordered), or ``None``."""
    sliced = dq.first_queryset(queryset)[:1]
    rows = (
        sliced if isinstance(sliced, list) else await evaluate_list(sliced)
    )  # list: a result cache
    return rows[0] if rows else None


async def evaluate_count(queryset: Any) -> int:
    """``QuerySet.count()``: the length of the result cache, else ``COUNT(*)``."""
    cached = dq.cached_result(queryset)
    if cached is not None:
        return len(cached)
    alias = resolve_alias(queryset)
    return await run_core(count_core(queryset.query), alias)


async def evaluate_exists(queryset: Any) -> bool:
    """``QuerySet.exists()``: whether the result cache has rows, else a ``LIMIT 1`` query."""
    cached = dq.cached_result(queryset)
    if cached is not None:
        return bool(cached)
    alias = resolve_alias(queryset)
    return await run_core(exists_core(queryset.query), alias)
