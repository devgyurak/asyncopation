"""Evaluate a QuerySet through the pipeline: compile -> execute -> materialize (ADR-0002)."""

from __future__ import annotations

from typing import Any

from asyncopation._django import query as dq
from asyncopation.connection import lifecycle
from asyncopation.connection.executor import current_executor
from asyncopation.engine.compiled import compile_query
from asyncopation.engine.execute import execute_multi
from asyncopation.engine.materialize import materialize

__all__ = ["evaluate_list"]


async def evaluate_list(queryset: Any) -> list[Any]:
    """The result list of ``queryset`` (``await alist()``); fills the QuerySet's result cache.

    A pre-filled result cache is returned without I/O. ``prefetch_related`` lookups are not
    evaluated yet (Phase 4) and raise ``NotImplementedError``.
    """
    cached = dq.cached_result(queryset)
    if cached is not None:
        return list(cached)
    dq.iterable_kind(queryset)  # refuse unsupported iterables before any I/O
    if dq.has_prefetch(queryset):
        raise NotImplementedError("prefetch_related evaluation is not implemented yet (Phase 4)")
    alias = queryset.db
    if not lifecycle.is_configured_alias(alias):
        # What `connections[alias]` raises in the sync ORM, so error handling carries over.
        raise dq.ConnectionDoesNotExist(f"The connection '{alias}' doesn't exist.")
    # Opening first matters: the compile-only wrapper exists only once the alias's server info is
    # known. `pinned` is read right before compiling, after the last await that could change it.
    await lifecycle.ensure_driver(alias)
    executor = current_executor(alias)
    wrapper = lifecycle.compile_wrapper(alias)
    compiled = compile_query(queryset, wrapper, autocommit=not executor.pinned)
    rows = await execute_multi(compiled, executor)
    cached = dq.cached_result(queryset)
    if (
        cached is not None
    ):  # a concurrent alist() on this QuerySet finished first: share its objects
        return list(cached)
    result = materialize(queryset, compiled, rows)
    dq.set_cached_result(queryset, result)
    return list(result)
